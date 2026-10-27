"""The AUD/NZD golden supplement through the real pipeline (worker, queue, checks, routing) with the
answer a perfect reader would give, printed the way each document prints it (a bare $, A$/NZ$, or a
code). No model is called. It shows what the system does with Australian and New Zealand dollars end
to end: the currency and every amount are read exactly, and a clean, correct invoice is cleared."""

import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from generator import au_nz
from generator.generate import GOLDEN_DIR
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from intake.checks.duplicates import DEDUPE_JOB
from intake.checks.matching import MATCH_JOB
from intake.checks.pipeline import VALIDATE_JOB
from intake.checks.routing import ROUTE_JOB
from intake.db.models import Invoice, InvoiceException, InvoiceLine, Tenant
from intake.extract.pipeline import EXTRACT_JOB
from intake.extract.prompt import load_prompt
from intake.extract.recorded import RecordedClient, write_fixture
from intake.extract.service import cache_key
from intake.ingest.service import PROCESS_JOB, UploadIngestor
from intake.ingest.storage import LocalStorage
from intake.security import BankVault
from intake.seed import load_master
from intake.worker.handlers import HANDLERS, make_extract_handler
from intake.worker.runner import run_once
from tests.integration.test_extraction_pipeline import (
    BANK_KEY,
    MODEL,
    PIPELINE_TABLES,
    PRICE,
    make_settings,
    purge,
)
from tests.support.truth_payload import payload_from_truth

OUT = GOLDEN_DIR / "au_nz"
MASTER = json.loads((OUT / "master.json").read_text())
TENANT = uuid.UUID(MASTER["tenant"]["id"])
TRUTHS = [json.loads(p.read_text()) for p in sorted((OUT / "truth").glob("*.json"))]
STYLE = {i.id: i.money_style for i in au_nz.build().invoices}
TODAY = date(2026, 10, 20)


class Env:
    def __init__(self, engine: Engine, ids: dict[str, uuid.UUID]) -> None:
        self.engine, self.ids = engine, ids  # truth id -> invoice id

    def invoice(self, truth_id: str) -> Invoice:
        with Session(self.engine) as s:
            inv = s.get_one(Invoice, self.ids[truth_id])
            s.expunge(inv)
            return inv

    def lines(self, truth_id: str) -> list[InvoiceLine]:
        with Session(self.engine) as s:
            rows = s.scalars(
                select(InvoiceLine)
                .where(InvoiceLine.invoice_id == self.ids[truth_id])
                .order_by(InvoiceLine.line_no)
            ).all()
            s.expunge_all()
            return list(rows)

    def exceptions(self, truth_id: str) -> list[str]:
        with Session(self.engine) as s:
            return list(
                s.scalars(
                    select(InvoiceException.code).where(
                        InvoiceException.invoice_id == self.ids[truth_id]
                    )
                )
            )


@pytest.fixture(scope="module")
def env(migrated_db_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Env]:
    tmp = tmp_path_factory.mktemp("au-nz")
    engine = create_engine(migrated_db_url)
    purge(engine)
    settings = make_settings(
        migrated_db_url, tmp, default_tenant_id=MASTER["tenant"]["id"], validation_today=TODAY
    )
    storage = LocalStorage(settings.storage_dir)
    with Session(engine) as s:
        s.merge(
            Tenant(id=TENANT, name=MASTER["tenant"]["name"], settings=MASTER["tenant"]["settings"])
        )
        s.commit()
        load_master(s, MASTER, BankVault(BANK_KEY))
        s.commit()
    fixtures = tmp / "fixtures"
    ingestor = UploadIngestor(settings, storage)
    ids: dict[str, uuid.UUID] = {}
    for truth in TRUTHS:
        path: Path = OUT / "invoices" / truth["file"]
        content = path.read_bytes()
        with Session(engine) as s:
            res = ingestor.ingest(s, content=content, filename=path.name, source="test")
            s.commit()
        ids[truth["id"]] = res.invoice_id
        version = settings.extraction_prompt_version
        key = cache_key(hashlib.sha256(content).hexdigest(), MODEL, version, load_prompt(version))
        write_fixture(fixtures, key, payload_from_truth(truth, STYLE[truth["id"]]))
    client = RecordedClient(model=MODEL, directory=fixtures, price=PRICE)
    handlers = {
        PROCESS_JOB: HANDLERS[PROCESS_JOB],
        EXTRACT_JOB: make_extract_handler(lambda _: client),
        VALIDATE_JOB: HANDLERS[VALIDATE_JOB],
        DEDUPE_JOB: HANDLERS[DEDUPE_JOB],
        MATCH_JOB: HANDLERS[MATCH_JOB],
        ROUTE_JOB: HANDLERS[ROUTE_JOB],
    }
    while run_once(engine, storage, settings, handlers):
        pass
    yield Env(engine, ids)
    with engine.begin() as conn:
        for t in PIPELINE_TABLES:
            conn.execute(text(f"delete from {t}"))  # noqa: S608
        for t in ("receipt_lines", "goods_receipts", "po_lines", "purchase_orders", "suppliers"):
            conn.execute(text(f"delete from {t} where tenant_id = :t"), {"t": str(TENANT)})
    engine.dispose()


def ids(*qualities: str) -> list[str]:
    return [t["id"] for t in TRUTHS if t["doc_quality"] in qualities]


@pytest.mark.parametrize("truth", TRUTHS, ids=lambda t: f"{t['id']}-{STYLE[t['id']]}")
def test_the_currency_and_every_amount_are_read_exactly(env: Env, truth: dict[str, Any]) -> None:
    inv, h = env.invoice(truth["id"]), truth["header"]
    assert inv.status != "failed"
    assert inv.currency == h["currency"]
    assert (inv.subtotal_minor, inv.tax_minor, inv.total_minor) == (
        h["subtotal_minor"], h["tax_total_minor"], h["total_minor"],
    )  # fmt: skip
    assert [ln.amount_minor for ln in env.lines(truth["id"])] == [
        ln["amount_minor"] for ln in truth["lines"]
    ]
    assert [ln.unit_price_minor for ln in env.lines(truth["id"])] == [
        ln["unit_price_minor"] for ln in truth["lines"]
    ]


@pytest.mark.parametrize("truth_id", ids("clean"))
def test_a_clean_correct_invoice_is_cleared_with_nothing_to_review(env: Env, truth_id: str) -> None:
    inv = env.invoice(truth_id)
    assert (inv.status, inv.route) == ("cleared", "straight_through"), env.exceptions(truth_id)
    assert env.exceptions(truth_id) == []


@pytest.mark.parametrize("truth_id", ids("scanned", "photo"))
def test_a_scanned_or_photographed_invoice_may_wait_for_a_person_but_never_for_its_currency(
    env: Env, truth_id: str
) -> None:
    held = env.exceptions(truth_id)
    assert not {"CURRENCY_MISMATCH", "INVALID_CURRENCY", "TOTAL_MISMATCH", "TAX_MISMATCH"} & set(
        held
    ), held
