"""Stage 4 of the pipeline: run the validation rules and store every result.

Idempotent: only an invoice that is `extracted` is worked on. The results and the move to `checking`
commit together, so a crash leaves nothing behind and a re-run does nothing. The invoice stays in
`checking`: deciding what happens next (exceptions, routing) is M7. Bank details are compared as
keyed hashes and never appear in the results or the audit log.
"""

import uuid
from datetime import UTC, date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.config import Settings
from intake.core.statuses import ActorType, InvoiceStatus
from intake.core.validate import (
    InvoiceFacts,
    LineFacts,
    Outcome,
    SupplierRecord,
    ValidationSettings,
    validate,
)
from intake.db.invoices import set_invoice_status
from intake.db.models import (
    CheckResult,
    FieldExtraction,
    Invoice,
    InvoiceLine,
    Supplier,
    Tenant,
)
from intake.extract.service import db_now

VALIDATE_JOB = "validate_invoice"


class WrongTenant(Exception):
    """A job named an invoice that belongs to another tenant."""


def _field(session: Session, invoice_id: uuid.UUID, name: str) -> FieldExtraction | None:
    return session.execute(
        select(FieldExtraction).where(
            FieldExtraction.invoice_id == invoice_id, FieldExtraction.field == name
        )
    ).scalar_one_or_none()


def _supplier_records(session: Session, tenant_id: uuid.UUID) -> list[SupplierRecord]:
    rows = session.execute(select(Supplier).where(Supplier.tenant_id == tenant_id)).scalars()
    return [
        SupplierRecord(
            id=str(s.id), name=s.name, aliases=tuple(s.aliases), tax_id=s.tax_id,
            default_currency=s.default_currency, tax_rate_bp=s.tax_rate_bp,
            bank_account_hash=s.bank_account_hash,
        )
        for s in rows
    ]  # fmt: skip


def load_facts(session: Session, inv: Invoice) -> InvoiceFacts:
    lines = session.execute(
        select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id).order_by(InvoiceLine.line_no)
    ).scalars()
    currency = _field(session, inv.id, "currency")
    tax_id = _field(session, inv.id, "supplier_tax_id")
    bank = _field(session, inv.id, "supplier_bank_account")
    # "Nothing printed" only counts when the model was sure; an unread account must not look
    # like an unchanged one.
    signals = (bank.signals or {}) if bank else {}
    bank_absence_certain = (
        bank is not None
        and bank.raw_value is None
        and signals.get("missing") is True
        and signals.get("self_confidence") == "high"
    )
    return InvoiceFacts(
        currency=inv.currency,
        printed_currency=currency.raw_value if currency else None,
        invoice_date=inv.invoice_date, due_date=inv.due_date,
        subtotal_minor=inv.subtotal_minor, tax_minor=inv.tax_minor, total_minor=inv.total_minor,
        lines=[
            LineFacts(
                ln.line_no, Decimal(ln.qty) if ln.qty is not None else None, ln.unit_price_minor,
                ln.amount_minor, Decimal(ln.tax_rate) if ln.tax_rate is not None else None,
            )
            for ln in lines
        ],
        supplier_name=inv.supplier_name,
        supplier_tax_id=tax_id.normalized_value if tax_id else None,
        bank_account_hash=bank.normalized_value if bank else None,
        bank_absence_certain=bank_absence_certain,
    )  # fmt: skip


def _today(session: Session, settings: Settings) -> date:
    if settings.validation_today is not None:  # a fixed "as of" date, for tests and a stable demo
        return settings.validation_today
    return db_now(session).astimezone(UTC).date()


def validate_invoice(
    session: Session, settings: Settings, invoice_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> None:
    inv = session.get_one(Invoice, invoice_id)
    if inv.tenant_id != tenant_id:
        raise WrongTenant
    if InvoiceStatus(inv.status) is not InvoiceStatus.EXTRACTED:
        return  # already checked (the results and the status change commit together)
    set_invoice_status(
        session, inv, InvoiceStatus.CHECKING, actor_type=ActorType.SYSTEM, actor_id="worker"
    )
    tenant = session.get_one(Tenant, inv.tenant_id)
    today = _today(session, settings)
    match, results = validate(
        load_facts(session, inv),
        _supplier_records(session, inv.tenant_id),
        ValidationSettings.from_tenant(tenant.settings or {}),
        today,
    )
    for r in results:
        session.add(
            CheckResult(
                tenant_id=inv.tenant_id, invoice_id=inv.id, check_code=r.code.value,
                passed=r.passed, details={**r.details, "outcome": r.outcome.value},
                rule_version=r.version,
            )
        )  # fmt: skip
    if match.supplier is not None:
        inv.supplier_id = uuid.UUID(match.supplier.id)
    by_outcome = {o: [r.code.value for r in results if r.outcome is o] for o in Outcome}
    record_event(
        session, tenant_id=inv.tenant_id, invoice_id=inv.id, event_type="checks_completed",
        actor_type=ActorType.SYSTEM, actor_id="worker",
        data={
            "rule_version": results[0].version,
            "passed": len(by_outcome[Outcome.PASS]),
            "failed": by_outcome[Outcome.FAIL],
            "skipped": by_outcome[Outcome.SKIPPED],
            "supplier_matched_by": match.matched_by,
            "as_of": today.isoformat(),
            "as_of_overridden": settings.validation_today is not None,
        },
    )  # fmt: skip
