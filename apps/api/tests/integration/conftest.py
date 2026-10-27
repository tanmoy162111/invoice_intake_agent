import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from intake.config import get_settings

ADMIN_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://intake:intake@localhost:5434/intake"
)
API_DIR = Path(__file__).resolve().parents[2]


def _admin_engine() -> Engine:
    return create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="session")
def migrated_db_url() -> Iterator[str]:
    """A fresh database with every migration applied. Skips locally if Postgres is down."""
    name = f"intake_test_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Postgres not reachable at TEST_DATABASE_URL: {exc.__class__.__name__}")
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        command.upgrade(Config(str(API_DIR / "alembic.ini")), "head")
        yield url
    finally:
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def engine(migrated_db_url: str) -> Iterator[Engine]:
    eng = create_engine(migrated_db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s
        s.rollback()


ACCOUNTING_MODULES = ("test_handoff_service", "test_handoff_api", "test_accounting_tokens")


@pytest.fixture(autouse=True)
def _remove_accounting_test_rows(request: pytest.FixtureRequest) -> Iterator[None]:
    """The accounting tests commit real rows (they exercise claims, locks and threads). The other
    suites share the database and assume it holds only their own rows, so remove everything these
    tests made. Tenants stay: audit_events (append-only) references them."""
    yield
    if request.module.__name__.rsplit(".", 1)[-1] not in ACCOUNTING_MODULES:
        return
    eng = create_engine(request.getfixturevalue("migrated_db_url"))
    mine = "select id from tenants where name like 'acct-test-%'"
    with eng.begin() as conn:
        for table in (
            "accounting_handoffs",
            "supplier_accounting_links",
            "accounting_connections",
            "invoice_lines",
            "invoices",
            "documents",
            "suppliers",
        ):
            conn.execute(text(f"delete from {table} where tenant_id in ({mine})"))
    eng.dispose()
