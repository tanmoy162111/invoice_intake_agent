"""What a reviewer does to an invoice (playbook M8): correct a field, close an exception, approve,
reject, ask for information, reveal a bank account.

Every action is one transaction owned by the caller: the change, a `review_actions` row (the human
decision) and audit events (codes and field names only, never values or invoice text) commit
together. The rules live in `core/review.py`; this module loads the facts and applies them. Nothing
here pays or moves money: approving only marks the invoice ready for export (M12).
"""

import uuid
from typing import Any

from cryptography.fernet import InvalidToken
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.checks.duplicates import detect_duplicates
from intake.checks.matching import match_invoice
from intake.checks.pipeline import validate_invoice
from intake.checks.routing import route_invoice
from intake.config import Settings
from intake.core.exceptions import ExceptionCode, Severity
from intake.core.review import (
    ExceptionState,
    ReviewProblem,
    approval_problem,
    clean_note,
    correct_problem,
    normalize_correction,
    reject_problem,
    request_info_problem,
    resolution_problem,
)
from intake.core.statuses import ActorType, ExceptionStatus, InvoiceStatus, ReviewAction
from intake.db.invoices import set_invoice_status
from intake.db.models import (
    CheckResult,
    FieldExtraction,
    Invoice,
    InvoiceException,
    InvoiceLine,
    ReviewActionRow,
)
from intake.security import BankVault

_BANK_FIELD = "supplier_bank_account"


class NotFound(Exception):
    """No such invoice or exception for this tenant (a different tenant's is the same as none)."""


class Unreadable(Exception):
    """The stored bank account cannot be decrypted with the configured key."""


class ReviewRefused(Exception):
    def __init__(self, problem: ReviewProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


def _invoice(session: Session, tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    inv = session.get(Invoice, invoice_id, with_for_update=True)
    if inv is None or inv.tenant_id != tenant_id:
        raise NotFound
    return inv


def _exceptions(session: Session, inv: Invoice) -> list[InvoiceException]:
    return list(
        session.execute(
            select(InvoiceException)
            .where(
                InvoiceException.tenant_id == inv.tenant_id, InvoiceException.invoice_id == inv.id
            )
            .order_by(InvoiceException.created_at, InvoiceException.id)
        ).scalars()
    )


def _act(
    session: Session, inv: Invoice, actor: str, action: ReviewAction, payload: dict[str, Any]
) -> None:
    session.add(
        ReviewActionRow(
            tenant_id=inv.tenant_id, invoice_id=inv.id, user_id=actor, action=action.value,
            payload=payload,
        )
    )  # fmt: skip


def _refuse(problem: ReviewProblem | None) -> None:
    if problem is not None:
        raise ReviewRefused(problem)


# ---- correcting a field: change it, then run every check again ---------------------------------


def _recheck(session: Session, settings: Settings, inv: Invoice, actor: str) -> None:
    """Close what is open, forget the old results, and run the whole pipeline again. The checks
    read the corrected values; a person's earlier decisions on identical exceptions are kept."""
    for ex in _exceptions(session, inv):
        if ex.status != ExceptionStatus.OPEN.value:
            continue
        ex.status = ExceptionStatus.RESOLVED.value
        ex.resolved_by = "system"
        ex.resolution_note = "Re-checked after a correction."
        record_event(
            session, tenant_id=inv.tenant_id, invoice_id=inv.id, event_type="exception_closed",
            actor_type=ActorType.SYSTEM, actor_id="worker",
            data={"code": ex.code, "resolution": "superseded"},
        )  # fmt: skip
    session.execute(
        delete(CheckResult).where(
            CheckResult.tenant_id == inv.tenant_id, CheckResult.invoice_id == inv.id
        )
    )
    session.execute(
        update(InvoiceLine)
        .where(InvoiceLine.tenant_id == inv.tenant_id, InvoiceLine.invoice_id == inv.id)
        .values(matched_po_line_id=None)
    )
    inv.supplier_id = None  # linked again by the checks, from the corrected values
    inv.route = None
    set_invoice_status(
        session, inv, InvoiceStatus.EXTRACTED, actor_type=ActorType.USER, actor_id=actor,
        reason="corrected",
    )  # fmt: skip
    validate_invoice(session, settings, inv.id, tenant_id=inv.tenant_id)
    # Never wait for other invoices here: a person is at the screen and expects the answer now.
    detect_duplicates(session, settings, inv.id, tenant_id=inv.tenant_id, stop_waiting=True)
    match_invoice(session, settings, inv.id, tenant_id=inv.tenant_id, stop_waiting=True)
    route_invoice(session, settings, inv.id, tenant_id=inv.tenant_id)


def _has_amounts(session: Session, inv: Invoice) -> bool:
    """Were any amounts read (in the invoice's current currency)? Then the currency is fixed."""
    if any(v is not None for v in (inv.subtotal_minor, inv.tax_minor, inv.total_minor)):
        return True
    return (
        session.scalars(
            select(InvoiceLine.id)
            .where(
                InvoiceLine.tenant_id == inv.tenant_id,
                InvoiceLine.invoice_id == inv.id,
                (InvoiceLine.unit_price_minor.is_not(None))
                | (InvoiceLine.amount_minor.is_not(None)),
            )
            .limit(1)
        ).first()
        is not None
    )


def correct_field(
    session: Session,
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    actor: str,
    field: str,
    value: str,
) -> None:
    inv = _invoice(session, tenant_id, invoice_id)
    _refuse(correct_problem(InvoiceStatus(inv.status)))
    correction = normalize_correction(field, value, inv.currency, _has_amounts(session, inv))
    if isinstance(correction, ReviewProblem):
        raise ReviewRefused(correction)
    row = session.execute(
        select(FieldExtraction).where(
            FieldExtraction.tenant_id == inv.tenant_id,
            FieldExtraction.invoice_id == inv.id,
            FieldExtraction.field == field,
        )
    ).scalar_one_or_none()
    if row is None:  # a field the reader never found, now supplied by a person
        row = FieldExtraction(
            tenant_id=inv.tenant_id, invoice_id=inv.id, field=field, signals={"corrected": True}
        )
        session.add(row)
    before = row.normalized_value
    row.corrected_value = value.strip() or None
    row.corrected_by = actor
    row.normalized_value = correction.text
    if correction.column is not None:
        setattr(inv, correction.column, correction.value)
    _act(
        session, inv, actor, ReviewAction.CORRECT_FIELD,
        {"field": field, "before": before, "after": correction.text},
    )  # fmt: skip
    record_event(
        session, tenant_id=inv.tenant_id, invoice_id=inv.id, event_type="field_corrected",
        actor_type=ActorType.USER, actor_id=actor, data={"field": field},
    )  # fmt: skip
    _recheck(session, settings, inv, actor)


# ---- closing an exception ----------------------------------------------------------------------


def close_exception(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    exception_id: uuid.UUID,
    actor: str,
    resolution: str,
    note: str | None,
) -> None:
    """`resolution` is "resolved" (the problem was dealt with) or "dismissed" (not a problem)."""
    ex = session.get(InvoiceException, exception_id)
    if ex is None or ex.tenant_id != tenant_id:
        raise NotFound
    inv = _invoice(session, tenant_id, ex.invoice_id)  # take the lock first...
    session.refresh(ex)  # ...then read the exception as it is now, not as it was when we loaded it
    if resolution not in (ExceptionStatus.RESOLVED.value, ExceptionStatus.DISMISSED.value):
        raise ValueError("resolution must be 'resolved' or 'dismissed'")
    _refuse(correct_problem(InvoiceStatus(inv.status)))
    if ex.status != ExceptionStatus.OPEN.value:
        raise ReviewRefused(ReviewProblem.WRONG_STATUS)
    _refuse(resolution_problem(Severity(ex.severity), note))
    text = clean_note(note) or None
    ex.status, ex.resolved_by, ex.resolution_note = resolution, actor, text
    _act(
        session, inv, actor, ReviewAction.DISMISS_EXCEPTION,
        {"exception_id": str(ex.id), "code": ex.code, "resolution": resolution, "note": text},
    )  # fmt: skip
    record_event(
        session, tenant_id=inv.tenant_id, invoice_id=inv.id, event_type="exception_closed",
        actor_type=ActorType.USER, actor_id=actor,
        data={"code": ex.code, "resolution": resolution},
    )  # fmt: skip


# ---- the decision ------------------------------------------------------------------------------


def approve_invoice(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    actor: str,
    note: str | None = None,
) -> None:
    inv = _invoice(session, tenant_id, invoice_id)
    states = [
        ExceptionState(ExceptionCode(e.code), Severity(e.severity), ExceptionStatus(e.status))
        for e in _exceptions(session, inv)
    ]
    _refuse(approval_problem(InvoiceStatus(inv.status), states))
    set_invoice_status(
        session, inv, InvoiceStatus.APPROVED, actor_type=ActorType.USER, actor_id=actor
    )
    _act(session, inv, actor, ReviewAction.APPROVE, {"note": clean_note(note) or None})


def reject_invoice(
    session: Session, *, tenant_id: uuid.UUID, invoice_id: uuid.UUID, actor: str, reason: str | None
) -> None:
    inv = _invoice(session, tenant_id, invoice_id)
    _refuse(reject_problem(InvoiceStatus(inv.status), reason))
    set_invoice_status(
        session, inv, InvoiceStatus.REJECTED, actor_type=ActorType.USER, actor_id=actor
    )
    _act(session, inv, actor, ReviewAction.REJECT, {"reason": clean_note(reason)})


def request_info(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    actor: str,
    note: str | None = None,
) -> None:
    """Records that the supplier or the requester was asked for something. No status changes."""
    inv = _invoice(session, tenant_id, invoice_id)
    _refuse(request_info_problem(InvoiceStatus(inv.status), note))
    _act(session, inv, actor, ReviewAction.REQUEST_INFO, {"note": clean_note(note) or None})
    record_event(
        session, tenant_id=inv.tenant_id, invoice_id=inv.id, event_type="info_requested",
        actor_type=ActorType.USER, actor_id=actor,
    )  # fmt: skip


def reveal_bank_account(
    session: Session, vault: BankVault, *, tenant_id: uuid.UUID, invoice_id: uuid.UUID, actor: str
) -> str:
    """The account as read from the invoice. Showing it is logged; the account itself never is."""
    inv = _invoice(session, tenant_id, invoice_id)
    token = session.execute(
        select(FieldExtraction.raw_value).where(
            FieldExtraction.tenant_id == inv.tenant_id,
            FieldExtraction.invoice_id == inv.id,
            FieldExtraction.field == _BANK_FIELD,
        )
    ).scalar_one_or_none()
    if not token:
        raise NotFound
    try:
        account = vault.decrypt(token)
    except InvalidToken:  # a wrong key or a damaged value: say so, and keep a record of the attempt
        record_event(
            session, tenant_id=inv.tenant_id, invoice_id=inv.id,
            event_type="bank_details_reveal_failed", actor_type=ActorType.USER, actor_id=actor,
        )  # fmt: skip
        raise Unreadable from None
    record_event(
        session, tenant_id=inv.tenant_id, invoice_id=inv.id, event_type="bank_details_revealed",
        actor_type=ActorType.USER, actor_id=actor,
    )  # fmt: skip
    return account
