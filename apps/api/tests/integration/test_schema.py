import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.core.statuses import ActorType
from intake.db.models import Tenant

GLOBAL_TABLES = {"alembic_version", "tenants", "eval_runs"}
EXPECTED_TABLES = {
    "tenants", "suppliers", "purchase_orders", "po_lines", "goods_receipts", "receipt_lines",
    "documents", "invoices", "invoice_lines", "field_extractions", "check_results",
    "exceptions", "review_actions", "audit_events", "llm_calls", "jobs", "eval_runs",
}  # fmt: skip


def test_all_playbook_tables_exist(engine: Engine) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            text("select table_name from information_schema.tables where table_schema='public'")
        ).scalars()
        assert set(rows) >= EXPECTED_TABLES


def test_every_business_table_has_non_null_tenant_id(engine: Engine) -> None:
    with engine.connect() as conn:
        tables = set(
            conn.execute(
                text("select table_name from information_schema.tables where table_schema='public'")
            ).scalars()
        )
        for table in tables - GLOBAL_TABLES:
            nullable = conn.execute(
                text(
                    "select is_nullable from information_schema.columns "
                    "where table_name=:t and column_name='tenant_id'"
                ),
                {"t": table},
            ).scalar()
            assert nullable == "NO", f"{table}.tenant_id missing or nullable"


@pytest.fixture
def audit_event_id(session: Session) -> tuple[uuid.UUID, int]:
    tenant = Tenant(name="t")
    session.add(tenant)
    session.flush()
    event = record_event(
        session,
        tenant_id=tenant.id,
        event_type="test.created",
        actor_type=ActorType.SYSTEM,
    )
    session.commit()
    return tenant.id, event.id


def test_audit_events_can_be_inserted(
    session: Session, audit_event_id: tuple[uuid.UUID, int]
) -> None:
    _, event_id = audit_event_id
    assert (
        session.execute(
            text("select count(*) from audit_events where id=:i"), {"i": event_id}
        ).scalar()
        == 1
    )


@pytest.mark.parametrize(
    "sql",
    [
        "update audit_events set event_type='tampered' where id=:i",
        "delete from audit_events where id=:i",
    ],
)
def test_audit_trigger_blocks_update_and_delete(
    session: Session, audit_event_id: tuple[uuid.UUID, int], sql: str
) -> None:
    _, event_id = audit_event_id
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text(sql), {"i": event_id})
    session.rollback()


def test_audit_trigger_blocks_truncate(
    session: Session, audit_event_id: tuple[uuid.UUID, int]
) -> None:
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("truncate audit_events"))
    session.rollback()


def test_invalid_status_is_rejected_by_check_constraint(session: Session) -> None:
    tenant = Tenant(name="t2")
    session.add(tenant)
    session.flush()
    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "insert into audit_events (tenant_id, actor_type, event_type) "
                "values (:t, 'robot', 'x')"
            ),
            {"t": tenant.id},
        )
    session.rollback()
