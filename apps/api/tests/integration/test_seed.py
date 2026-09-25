import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from intake.db.models import (
    AuditEvent,
    GoodsReceipt,
    PoLine,
    PurchaseOrder,
    ReceiptLine,
    Supplier,
)
from intake.security import BankVault
from intake.seed import DEFAULT_SEED_DIR, copy_inbox, load_master

MASTER = json.loads((DEFAULT_SEED_DIR / "master.json").read_text())


@pytest.fixture
def vault() -> BankVault:
    return BankVault(Fernet.generate_key().decode())


@pytest.fixture
def clean_db(engine: Engine) -> None:
    """Remove the seed's child rows. Tenants stay: audit_events (append-only) references them."""
    with engine.begin() as conn:
        for table in (
            "receipt_lines",
            "goods_receipts",
            "po_lines",
            "purchase_orders",
            "suppliers",
        ):
            conn.execute(
                text(f"delete from {table} where tenant_id = :t"), {"t": MASTER["tenant"]["id"]}
            )


def counts(session: Session) -> dict[str, int]:
    return {
        m.__tablename__: session.scalar(select(func.count()).select_from(m)) or 0
        for m in (Supplier, PurchaseOrder, PoLine, GoodsReceipt, ReceiptLine)
    }


def test_seed_loads_and_is_idempotent(session: Session, vault: BankVault, clean_db: None) -> None:
    first = load_master(session, MASTER, vault)
    session.commit()
    after_first = counts(session)
    assert first > 0
    assert after_first["suppliers"] == len(MASTER["suppliers"])
    assert after_first["purchase_orders"] == len(MASTER["purchase_orders"])

    second = load_master(session, MASTER, vault)
    session.commit()
    assert second == 0
    assert counts(session) == after_first


def test_audit_event_written_once_per_change(
    session: Session, vault: BankVault, clean_db: None
) -> None:
    tenant_id = MASTER["tenant"]["id"]

    def events() -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.tenant_id == tenant_id, AuditEvent.event_type.like("seed.%"))
            )
            or 0
        )

    before = events()
    load_master(session, MASTER, vault)
    session.commit()
    assert events() == before + 1
    load_master(session, MASTER, vault)
    session.commit()
    assert events() == before + 1


def test_bank_details_are_encrypted_at_rest(
    session: Session, vault: BankVault, clean_db: None
) -> None:
    load_master(session, MASTER, vault)
    session.commit()
    plain = MASTER["suppliers"][0]["bank_account"]
    row = session.scalars(
        select(Supplier).where(Supplier.name == MASTER["suppliers"][0]["name"])
    ).one()
    assert row.bank_account_encrypted and plain not in row.bank_account_encrypted
    assert row.bank_account_hash == vault.hash(plain)
    assert vault.decrypt(row.bank_account_encrypted) == vault.decrypt(vault.encrypt(plain))


def test_copy_inbox_copies_invoices_but_not_truth(tmp_path: Path) -> None:
    n = copy_inbox(DEFAULT_SEED_DIR, tmp_path / "inbox")
    files = list((tmp_path / "inbox").iterdir())
    assert n == len(files) == len(list((DEFAULT_SEED_DIR / "invoices").iterdir()))
    assert not any(f.suffix == ".json" for f in files)
    assert copy_inbox(DEFAULT_SEED_DIR, tmp_path / "inbox") == 0


def test_seed_loads_each_suppliers_usual_tax_rate(
    session: Session, vault: BankVault, clean_db: None
) -> None:
    load_master(session, MASTER, vault)
    session.commit()
    rates = {s.name: s.tax_rate_bp for s in session.scalars(select(Supplier))}
    assert rates == {s["name"]: s["tax_rate_bp"] for s in MASTER["suppliers"]}
    assert all(bp is not None for bp in rates.values())
