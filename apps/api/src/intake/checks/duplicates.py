"""Stage 5 of the pipeline: is this invoice a duplicate of an earlier one? (playbook §6.5)

Runs after the validation checks, on an invoice in `checking`, and records one `POSSIBLE_DUPLICATE`
result naming the earlier invoice it collides with. Only the later-received invoice is compared;
the original is never flagged. Before comparing, the stage waits (a deferral, not a failure) for
earlier invoices that have not been read and checked yet, because their supplier and fields are not
known. If the wait runs out with some still pending, a clean comparison is reported as `skipped`,
never as a pass. Nothing else changes: the invoice stays in `checking` (routing is M7).
"""

import uuid
from datetime import timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.checks.pipeline import WrongTenant
from intake.config import Settings
from intake.core.dedupe import (
    POSSIBLE_DUPLICATE,
    RULE_VERSION,
    DedupeResult,
    DedupeSettings,
    InvoiceRef,
    check_duplicate,
    supplier_key,
)
from intake.core.statuses import ActorType, InvoiceStatus
from intake.core.validate import Outcome
from intake.db.models import CheckResult, Invoice, Tenant
from intake.extract.service import db_now
from intake.worker import queue

DEDUPE_JOB = "detect_duplicates"
WAITING = "WAITING_FOR_EARLIER_INVOICES"
# Not yet read (or not yet checked): their supplier and fields cannot be compared reliably.
_PENDING = (InvoiceStatus.RECEIVED, InvoiceStatus.EXTRACTING, InvoiceStatus.EXTRACTED)
_NOT_COMPARABLE = (*_PENDING, InvoiceStatus.FAILED)


def _ref(inv: Invoice) -> InvoiceRef:
    return InvoiceRef(
        id=str(inv.id),
        supplier=supplier_key(str(inv.supplier_id) if inv.supplier_id else None, inv.supplier_name),
        invoice_number=inv.invoice_number, invoice_date=inv.invoice_date,
        total_minor=inv.total_minor, currency=inv.currency,
    )  # fmt: skip


def _earlier(inv: Invoice):  # type: ignore[no-untyped-def]
    """Invoices of the tenant received before this one (ties broken by id)."""
    return and_(
        Invoice.tenant_id == inv.tenant_id,
        Invoice.id != inv.id,
        or_(
            Invoice.created_at < inv.created_at,
            and_(Invoice.created_at == inv.created_at, Invoice.id < inv.id),
        ),
    )


def detect_duplicates(
    session: Session,
    settings: Settings,
    invoice_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    stop_waiting: bool = False,
) -> queue.Deferral | None:
    inv = session.get_one(Invoice, invoice_id)
    if inv.tenant_id != tenant_id:
        raise WrongTenant
    if InvoiceStatus(inv.status) is not InvoiceStatus.CHECKING:
        return None  # the checks have not run, or the invoice is further along
    done = session.execute(
        select(CheckResult.id).where(
            CheckResult.invoice_id == inv.id, CheckResult.check_code == POSSIBLE_DUPLICATE
        )
    ).first()
    if done is not None:
        return None  # already checked: nothing to do

    earlier = _earlier(inv)
    pending = session.scalar(
        select(func.count()).select_from(Invoice).where(
            earlier, Invoice.status.in_([s.value for s in _PENDING])
        )
    ) or 0  # fmt: skip
    if pending and not stop_waiting:
        until = db_now(session) + timedelta(seconds=settings.dedupe_poll_s)
        return queue.Deferral(until, WAITING)

    same_supplier = (
        Invoice.supplier_id == inv.supplier_id if inv.supplier_id else Invoice.supplier_id.is_(None)
    )
    rows = session.execute(
        select(Invoice)
        .where(earlier, same_supplier, Invoice.status.not_in([s.value for s in _NOT_COMPARABLE]))
        .order_by(Invoice.created_at, Invoice.id)
    ).scalars()
    tenant = session.get_one(Tenant, inv.tenant_id)
    result = check_duplicate(
        _ref(inv), [_ref(r) for r in rows], DedupeSettings.from_tenant(tenant.settings or {})
    )
    if pending and result.outcome is Outcome.PASS:  # cannot be sure with invoices still unread
        result = DedupeResult(
            Outcome.SKIPPED, None, {"reason": "EARLIER_INVOICES_STILL_PENDING", **result.details}
        )
    details = {**result.details, "outcome": result.outcome.value}
    if pending:
        details["pending_earlier"] = pending
    session.add(
        CheckResult(
            tenant_id=inv.tenant_id, invoice_id=inv.id, check_code=POSSIBLE_DUPLICATE,
            passed=result.passed, details=details, rule_version=RULE_VERSION,
        )
    )  # fmt: skip
    match = result.match
    record_event(
        session, tenant_id=inv.tenant_id, invoice_id=inv.id,
        event_type="duplicate_check_completed", actor_type=ActorType.SYSTEM, actor_id="worker",
        data={
            "rule_version": RULE_VERSION, "outcome": result.outcome.value,
            "kind": match.kind.value if match else None,
            "existing_invoice_id": match.existing.id if match else None,
            "pending_earlier": pending,
        },
    )  # fmt: skip
    return None
