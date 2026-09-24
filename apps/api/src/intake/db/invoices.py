"""The one place that changes an invoice's status: checks the transition, audits it."""

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
