"""The one place that changes an invoice's status: checks the transition, audits it."""

import uuid

from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.core.statuses import ActorType, InvoiceStatus
from intake.core.workflow import can_transition
from intake.db.models import Invoice


class InvalidTransition(Exception):
    pass


def set_invoice_status(
    session: Session,
    invoice: Invoice,
    new: InvoiceStatus,
    *,
    actor_type: ActorType,
    actor_id: str | None = None,
    reason: str | None = None,
) -> None:
    current = InvoiceStatus(invoice.status)
    if not can_transition(current, new):
        raise InvalidTransition(f"{current.value} -> {new.value} is not allowed")
    invoice.status = new.value
    record_event(
        session,
        tenant_id=invoice.tenant_id,
        invoice_id=invoice.id,
        event_type="status_changed",
        actor_type=actor_type,
        actor_id=actor_id,
        data={"from": current.value, "to": new.value, **({"reason": reason} if reason else {})},
    )


def fail_extraction(session: Session, invoice: Invoice, reason: str) -> None:
    """extracting -> failed, with the reason in both the status change and an `extraction_failed`
    audit event. `reason` is a code or an exception class name, never document content."""
    set_invoice_status(
        session, invoice, InvoiceStatus.FAILED, actor_type=ActorType.SYSTEM, actor_id="worker",
        reason=reason,
    )  # fmt: skip
    record_event(
        session, tenant_id=invoice.tenant_id, invoice_id=invoice.id,
        event_type="extraction_failed", actor_type=ActorType.SYSTEM, actor_id="worker",
        data={"reason": reason},
    )  # fmt: skip


def fail_invoice_after_job_gave_up(session: Session, invoice_id: uuid.UUID, reason: str) -> None:
    """A job for this invoice used up its attempts. An invoice that never got extracted becomes
    `failed` (visible to a reviewer) instead of sitting in `received` forever. One that already
    got further is left alone."""
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        return
    if InvoiceStatus(invoice.status) is InvoiceStatus.RECEIVED:
        set_invoice_status(
            session, invoice, InvoiceStatus.EXTRACTING, actor_type=ActorType.SYSTEM,
            actor_id="worker",
        )  # fmt: skip
    if InvoiceStatus(invoice.status) is InvoiceStatus.EXTRACTING:
        fail_extraction(session, invoice, reason)
