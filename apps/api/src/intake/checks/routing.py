"""Stage 7 of the pipeline: raise exceptions and route the invoice (playbook §6.7).

Runs after the 3-way match, on an invoice in `checking`. It reads the stored check results, has the
pure classifier turn every failed or skipped check (plus doubtful critical fields and a total above
the approval limit) into an exception with its explanation and fix, then has the pure router decide
`cleared` or `needs_review`. Exceptions, the route and the status change commit together, and each
writes an audit event (codes and ids only, never invoice text). The status change is what makes the
stage idempotent: an invoice that is no longer `checking` is left alone.

A check with no stored result at all is not a pass: the invoice goes to review. Cleared invoices
still wait for a person to approve (approval is M8). Nothing here pays or moves money.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.checks.pipeline import WrongTenant
from intake.config import Settings
from intake.core.classify import CheckRow, ClassifyContext, WeakField, classify
from intake.core.confidence import CRITICAL_FIELDS, fields_below_threshold
from intake.core.match import MatchCode
from intake.core.routing import RoutedException, RoutingInput, approval_limit_for, decide_route
from intake.core.statuses import ActorType, InvoiceStatus
from intake.core.validate import CheckCode, Outcome
from intake.db.invoices import set_invoice_status
from intake.db.models import (
    CheckResult,
    FieldExtraction,
    Invoice,
    InvoiceException,
    Supplier,
    Tenant,
)

ROUTE_JOB = "route_invoice"
# Every check that must have a result before an invoice can be cleared.
EXPECTED_CHECKS = (
    *(c.value for c in CheckCode),
    "POSSIBLE_DUPLICATE",
    *(c.value for c in MatchCode),
)


def _version_number(version: str) -> int:
    """'v10' is newer than 'v9' (a text sort would say the opposite)."""
    digits = version.lstrip("vV")
    return int(digits) if digits.isdigit() else 0


def _outcome(row: CheckResult) -> Outcome:
    stored = row.details.get("outcome")
    if isinstance(stored, str) and stored in {o.value for o in Outcome}:
        return Outcome(stored)
    return Outcome.PASS if row.passed else Outcome.FAIL


def _check_rows(session: Session, inv: Invoice) -> list[CheckRow]:
    """One row per check code: the latest rule version wins."""
    latest: dict[str, CheckResult] = {}
    for r in sorted(
        session.execute(
            select(CheckResult).where(
                CheckResult.tenant_id == inv.tenant_id, CheckResult.invoice_id == inv.id
            )
        ).scalars(),
        key=lambda row: _version_number(row.rule_version),
    ):
        latest[r.check_code] = r
    return [CheckRow(code, _outcome(r), r.details) for code, r in latest.items()]


def _weak_fields(session: Session, inv: Invoice, minimum: Decimal) -> list[WeakField]:
    scores: dict[str, Decimal] = {}
    for f in session.execute(
        select(FieldExtraction).where(
            FieldExtraction.tenant_id == inv.tenant_id, FieldExtraction.invoice_id == inv.id
        )
    ).scalars():
        # a value a person has corrected is as good as certain
        if f.corrected_value is not None:
            scores[f.field] = Decimal(1)
        elif f.confidence is not None:
            scores[f.field] = f.confidence
    return [
        WeakField(name, scores.get(name), minimum)
        for name in fields_below_threshold(scores, CRITICAL_FIELDS, minimum)
    ]


def route_invoice(
    session: Session, settings: Settings, invoice_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> None:
    # Locked: a re-claimed job must not route the same invoice twice at the same time.
    inv = session.get_one(Invoice, invoice_id, with_for_update=True)
    if inv.tenant_id != tenant_id:
        raise WrongTenant
    if InvoiceStatus(inv.status) is not InvoiceStatus.CHECKING:
        return None  # not checked yet, or already routed: nothing to do

    tenant = session.get_one(Tenant, inv.tenant_id)
    supplier = (
        session.scalars(
            select(Supplier).where(
                Supplier.tenant_id == inv.tenant_id, Supplier.id == inv.supplier_id
            )
        ).first()
        if inv.supplier_id
        else None
    )
    rows = _check_rows(session, inv)
    limit = approval_limit_for(tenant.settings or {}, inv.currency)
    weak = _weak_fields(session, inv, settings.field_confidence_min)
    ctx = ClassifyContext(
        currency=inv.currency,
        total_minor=inv.total_minor,
        supplier_name=supplier.name if supplier else None,
        approval_limit_minor=limit,
        weak_fields=weak,
    )
    drafts = classify(rows, ctx)
    for d in drafts:
        session.add(
            InvoiceException(
                tenant_id=inv.tenant_id, invoice_id=inv.id, code=d.code.value,
                severity=d.severity.value, explanation=d.explanation,
                suggested_fix=d.suggested_fix,
            )
        )  # fmt: skip
        record_event(
            session, tenant_id=inv.tenant_id, invoice_id=inv.id, event_type="exception_raised",
            actor_type=ActorType.SYSTEM, actor_id="worker",
            data={"code": d.code.value, "severity": d.severity.value, "unchecked": d.unchecked},
        )  # fmt: skip

    present = {r.code for r in rows}
    decision = decide_route(
        RoutingInput(
            exceptions=[RoutedException(d.code, d.severity, d.unchecked) for d in drafts],
            weak_fields=[w.field for w in weak],
            missing_checks=[c for c in EXPECTED_CHECKS if c not in present],
            total_minor=inv.total_minor,
            approval_limit_minor=limit,
        )
    )
    reasons = [r.value for r in decision.reasons]
    inv.route = decision.route.value
    set_invoice_status(
        session, inv, decision.status, actor_type=ActorType.SYSTEM, actor_id="worker",
        reason=",".join(reasons) or None,
    )  # fmt: skip
    record_event(
        session, tenant_id=inv.tenant_id, invoice_id=inv.id, event_type="routing_decided",
        actor_type=ActorType.SYSTEM, actor_id="worker",
        data={
            "status": decision.status.value, "route": decision.route.value, "reasons": reasons,
            "exceptions": [d.code.value for d in drafts],
        },
    )  # fmt: skip
    return None
