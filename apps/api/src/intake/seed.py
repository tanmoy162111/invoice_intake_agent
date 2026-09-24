"""Load the synthetic master data and stage invoice files in an inbox folder.

Safe to re-run: rows have stable ids and are upserted; an audit event is written only when the
run created something new. Invoices themselves are not loaded here; they arrive through ingestion
(M2) from the inbox folder.
"""

import argparse
import json
import shutil
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.config import get_settings
from intake.core.statuses import ActorType
from intake.db.models import (
    GoodsReceipt,
    PoLine,
    PurchaseOrder,
    ReceiptLine,
    Supplier,
    Tenant,
)
from intake.db.session import session_scope
from intake.security import BankVault

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SEED_DIR = REPO_ROOT / "data" / "seed"
DEFAULT_INBOX = REPO_ROOT / "data" / "inbox"


def _upsert(session: Session, obj: Any, created: list[int]) -> None:
    if session.get(type(obj), obj.id) is None:
        created[0] += 1
    session.merge(obj)


def load_master(session: Session, master: dict[str, Any], vault: BankVault) -> int:
    """Upsert tenant, suppliers, POs and receipts. Returns how many rows were newly created."""
    created = [0]
    tenant_id = uuid.UUID(master["tenant"]["id"])
    _upsert(
        session,
        Tenant(id=tenant_id, name=master["tenant"]["name"], settings=master["tenant"]["settings"]),
        created,
    )
    session.flush()
    supplier_ids: dict[str, uuid.UUID] = {}
    for s in master["suppliers"]:
        sid = uuid.UUID(s["id"])
        supplier_ids[s["key"]] = sid
        _upsert(
            session,
            Supplier(
                id=sid,
                tenant_id=tenant_id,
                name=s["name"],
                aliases=s["aliases"],
                tax_id=s["tax_id"],
                bank_account_hash=vault.hash(s["bank_account"]),
                bank_account_encrypted=vault.encrypt(s["bank_account"]),
                default_currency=s["default_currency"],
                is_active=True,
            ),
            created,
        )
    session.flush()
    for po in master["purchase_orders"]:
        po_id = uuid.UUID(po["id"])
        _upsert(
            session,
            PurchaseOrder(
                id=po_id,
                tenant_id=tenant_id,
                po_number=po["po_number"],
                supplier_id=supplier_ids[po["supplier_key"]],
                currency=po["currency"],
                total_minor=po["total_minor"],
                status="open",
            ),
            created,
        )
        session.flush()
        line_ids: dict[int, uuid.UUID] = {}
        for ln in po["lines"]:
            line_ids[ln["line_no"]] = uuid.UUID(ln["id"])
            _upsert(
                session,
                PoLine(
                    id=line_ids[ln["line_no"]],
                    tenant_id=tenant_id,
                    po_id=po_id,
                    line_no=ln["line_no"],
                    description=ln["description"],
                    sku=ln["sku"],
                    qty=Decimal(ln["qty"]),
                    unit_price_minor=ln["unit_price_minor"],
                ),
                created,
            )
        session.flush()
        for r in po["receipts"]:
            receipt_id = uuid.UUID(r["id"])
            _upsert(
                session,
                GoodsReceipt(
                    id=receipt_id,
                    tenant_id=tenant_id,
                    po_id=po_id,
                    received_at=_at(r["received_at"]),
                ),
                created,
            )
            session.flush()
            for rl in r["lines"]:
                rl_id = uuid.uuid5(receipt_id, str(rl["line_no"]))
                _upsert(
                    session,
                    ReceiptLine(
                        id=rl_id,
                        tenant_id=tenant_id,
                        receipt_id=receipt_id,
                        po_line_id=line_ids[rl["line_no"]],
                        qty_received=Decimal(rl["qty_received"]),
                    ),
                    created,
                )
    if created[0]:
        record_event(
            session,
            tenant_id=tenant_id,
            event_type="seed.master_data_loaded",
            actor_type=ActorType.SYSTEM,
            actor_id="intake.seed",
            data={
                "new_rows": created[0],
                "suppliers": len(master["suppliers"]),
                "purchase_orders": len(master["purchase_orders"]),
            },
        )
    return created[0]


def _at(day: str) -> Any:
    from datetime import UTC, datetime

    return datetime.fromisoformat(day).replace(tzinfo=UTC)


def copy_inbox(seed_dir: Path, inbox: Path) -> int:
    """Copy invoice files (never the ground truth) into the inbox. Returns files copied."""
    inbox.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in sorted((seed_dir / "invoices").iterdir()):
        dest = inbox / src.name
        if not dest.exists() or dest.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dest)
            copied += 1
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument("--inbox", type=Path, default=DEFAULT_INBOX)
    args = parser.parse_args()

    master = json.loads((args.seed_dir / "master.json").read_text())
    vault = BankVault(get_settings().bank_encryption_key)
    with session_scope() as session:
        new_rows = load_master(session, master, vault)
    copied = copy_inbox(args.seed_dir, args.inbox)
    print(f"master data: {new_rows} new rows; inbox: {copied} files copied to {args.inbox}")


if __name__ == "__main__":
    main()
