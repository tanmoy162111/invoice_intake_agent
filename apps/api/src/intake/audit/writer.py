"""Append-only audit writer. There is deliberately no update or delete function here."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from intake.core.statuses import ActorType
from intake.db.models import AuditEvent


def record_event(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    actor_type: ActorType,
    actor_id: str | None = None,
    invoice_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant_id,
        invoice_id=invoice_id,
        actor_type=actor_type.value,
        actor_id=actor_id,
        event_type=event_type,
        data=data or {},
    )
    session.add(event)
    session.flush()
    return event
