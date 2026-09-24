"""M3 acceptance: every clean seed invoice is read into invoices, invoice_lines and
field_extractions, end to end (real PDFs, real worker, real queue), with recorded model answers."""

import json
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text, update
from sqlalchemy.orm import Session

from intake.config import Settings
from intake.core.llm_budget import ModelPrice, request_hash
from intake.db.models import (
    AuditEvent,
    Document,
    FieldExtraction,
    Invoice,
    InvoiceLine,
    Job,
    LlmCall,
)
from intake.extract.pipeline import EXTRACT_JOB, extract_invoice
from intake.extract.recorded import RecordedClient, write_fixture
from intake.ingest.service import PROCESS_JOB, UploadIngestor
from intake.ingest.storage import LocalStorage
from intake.main import create_app
from intake.security import BankVault
from intake.seed import DEFAULT_SEED_DIR, load_master
from intake.worker.handlers import HANDLERS, make_extract_handler
from intake.worker.runner import run_once
from tests.support.truth_payload import (
    load_truths,
    payload_from_truth,
    seed_file,
)

MODEL = "claude-sonnet-5"
PRICE = ModelPrice(2_000_000, 10_000_000)
MASTER = json.loads((DEFAULT_SEED_DIR / "master.json").read_text())
TRUTHS = load_truths("clean")
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BANK_KEY = Fernet.generate_key().decode()
PIPELINE_TABLES = (
    "jobs", "llm_calls", "field_extractions", "invoice_lines", "invoices", "documents",
)  # fmt: skip


def make_settings(url: str, tmp: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": url, "storage_dir": str(tmp / "storage"), "api_token": TOKEN,
        "job_backoff_base_s": 0, "render_dpi": 100, "anthropic_api_key": "",
        "bank_encryption_key": BANK_KEY, "extraction_model": MODEL,
    }  # fmt: skip
    return Settings(**{**base, **kw})


def drain(engine: Engine, storage: LocalStorage, settings: Settings, client: RecordedClient) -> int:
    handlers = {
        PROCESS_JOB: HANDLERS[PROCESS_JOB],
        EXTRACT_JOB: make_extract_handler(lambda _: client),
    }
    n = 0
    while run_once(engine, storage, settings, handlers):
        n += 1
    return n


def ingest_all(engine: Engine, settings: Settings, storage: LocalStorage, fixtures: Path,
               truths: list[dict[str, Any]]) -> dict[str, uuid.UUID]:  # fmt: skip
    """Upload each seed file and record its 'model answer'. Returns file name -> invoice id."""
    ingestor = UploadIngestor(settings, storage)
    ids: dict[str, uuid.UUID] = {}
    for truth in truths:
        path = seed_file(truth)
        with Session(engine) as s:
            res = ingestor.ingest(s, content=path.read_bytes(), filename=path.name, source="test")
            s.commit()
        ids[truth["file"]] = res.invoice_id
        key = request_hash(res_sha(path), MODEL, settings.extraction_prompt_version)
        write_fixture(fixtures, key, payload_from_truth(truth))
    return ids


def res_sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


class Env:
    def __init__(self, engine: Engine, settings: Settings, storage: LocalStorage,
                 client: RecordedClient, ids: dict[str, uuid.UUID]) -> None:  # fmt: skip
        self.engine, self.settings, self.storage, self.client, self.ids = (
            engine, settings, storage, client, ids,
        )  # fmt: skip


@pytest.fixture(scope="module")
def env(migrated_db_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Env]:
    tmp = tmp_path_factory.mktemp("m3")
    engine = create_engine(migrated_db_url)
    settings = make_settings(migrated_db_url, tmp, default_tenant_id=MASTER["tenant"]["id"])
    storage = LocalStorage(settings.storage_dir)
    with Session(engine) as s:  # supplier master data, for the master-data confidence signal
        from intake.db.models import Tenant

        s.merge(Tenant(id=uuid.UUID(MASTER["tenant"]["id"]), name=MASTER["tenant"]["name"]))
        s.commit()
        load_master(s, MASTER, BankVault(BANK_KEY))
        s.commit()
    fixtures = tmp / "fixtures"
    ids = ingest_all(engine, settings, storage, fixtures, TRUTHS)
    client = RecordedClient(model=MODEL, directory=fixtures, price=PRICE)
    drain(engine, storage, settings, client)
    yield Env(engine, settings, storage, client, ids)
    with engine.begin() as conn:
        for t in PIPELINE_TABLES:
            conn.execute(text(f"delete from {t}"))
        for t in ("receipt_lines", "goods_receipts", "po_lines", "purchase_orders", "suppliers"):
            conn.execute(
                text(f"delete from {t} where tenant_id = :t"), {"t": MASTER["tenant"]["id"]}
            )
    engine.dispose()


def by_file(env: Env, truth: dict[str, Any]) -> uuid.UUID:
    return env.ids[truth["file"]]


def test_every_clean_seed_invoice_is_extracted(env: Env) -> None:
    assert len(TRUTHS) == 59
    with Session(env.engine) as s:
        for truth in TRUTHS:
            inv = s.get_one(Invoice, by_file(env, truth))
            h = truth["header"]
            assert inv.status == "extracted", truth["file"]
            assert (inv.invoice_number, inv.currency) == (h["invoice_number"], h["currency"])
            assert (
                inv.invoice_date is not None and inv.invoice_date.isoformat() == h["invoice_date"]
            )
            assert inv.due_date is not None and inv.due_date.isoformat() == h["due_date"]
            assert (inv.subtotal_minor, inv.tax_minor, inv.total_minor) == (
                h["subtotal_minor"], h["tax_total_minor"], h["total_minor"],
            )  # fmt: skip
            assert inv.po_number == h["po_number"]
            assert inv.supplier_name == h["supplier_name"]
            query = select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
            lines = s.execute(query.order_by(InvoiceLine.line_no)).scalars().all()
            assert [(x.line_no, x.qty, x.unit_price_minor, x.amount_minor) for x in lines] == [
                (
                    t["line_no"],
                    Decimal(str(t["quantity"])),
                    t["unit_price_minor"],
                    t["amount_minor"],
                )
                for t in truth["lines"]
            ]
            n_fields = s.scalar(
                select(func.count()).select_from(FieldExtraction).where(
                    FieldExtraction.invoice_id == inv.id
                )
            )  # fmt: skip
            assert n_fields == 13 + 6 * len(truth["lines"])


def test_a_missing_po_number_is_null_not_made_up(env: Env) -> None:
    no_po = [t for t in TRUTHS if not t["header"]["po_number"]]
    assert no_po, "the seed set must include clean invoices without a PO number"
    with Session(env.engine) as s:
        for truth in no_po:
            inv = s.get_one(Invoice, by_file(env, truth))
            assert inv.po_number is None
            row = s.execute(
                select(FieldExtraction).where(
                    FieldExtraction.invoice_id == inv.id, FieldExtraction.field == "po_number"
                )
            ).scalar_one()
            assert (row.raw_value, row.normalized_value, row.confidence) == (None, None, Decimal(0))


def test_invoices_without_planted_problems_have_trusted_critical_fields(env: Env) -> None:
    good = [t for t in TRUTHS if not t["expected"]["must_raise"]]
    assert len(good) >= 30
    with Session(env.engine) as s:
        weak = {}
        for truth in good:
            ev = s.execute(
                select(AuditEvent).where(
                    AuditEvent.invoice_id == by_file(env, truth),
                    AuditEvent.event_type == "extraction_completed",
                )
            ).scalar_one()
            if ev.data["low_confidence_critical_fields"]:
                weak[truth["file"]] = ev.data["low_confidence_critical_fields"]
    assert weak == {}


def test_every_status_change_and_extraction_is_audited_in_order(env: Env) -> None:
    truth = TRUTHS[0]
    with Session(env.engine) as s:
        events = s.execute(
            select(AuditEvent).where(AuditEvent.invoice_id == by_file(env, truth))
            .order_by(AuditEvent.created_at, AuditEvent.id)
        ).scalars().all()  # fmt: skip
    kinds = [(e.event_type, e.data.get("to")) for e in events]
    assert kinds == [
        ("document_received", None), ("status_changed", "received"), ("document_processed", None),
        ("status_changed", "extracting"), ("status_changed", "extracted"),
        ("extraction_completed", None),
    ]  # fmt: skip
    done = events[-1]
    assert (done.actor_type, done.actor_id) == ("agent", MODEL)
    assert done.data["cached"] is False and done.data["model_calls"] == 1


def test_bank_accounts_are_never_stored_in_the_clear(env: Env) -> None:
    vault = BankVault(BANK_KEY)
    truth = next(t for t in TRUTHS if t["header"]["supplier_bank_account"])
    plain = truth["header"]["supplier_bank_account"]
    digits = "".join(ch for ch in plain if ch.isdigit())
    with Session(env.engine) as s:
        row = s.execute(
            select(FieldExtraction).where(
                FieldExtraction.invoice_id == by_file(env, truth),
                FieldExtraction.field == "supplier_bank_account",
            )
        ).scalar_one()
        assert row.raw_value is not None and digits not in row.raw_value
        assert vault.decrypt(row.raw_value).isalnum()
        assert row.normalized_value == vault.hash(plain)
        assert (row.confidence or 0) > 0
        places = (
            ("llm_calls", "response"),
            ("audit_events", "data"),
            ("field_extractions", "signals"),
            ("invoices", "supplier_name"),
        )
        for table, column in places:
            hits = s.execute(
                text(f"select count(*) from {table} where {column}::text like :p"),
                {"p": f"%{digits[-8:]}%"},
            ).scalar_one()
            assert hits == 0, f"bank digits found in {table}.{column}"


def test_every_call_is_logged_once_with_cost(env: Env) -> None:
    with Session(env.engine) as s:
        rows = s.execute(select(LlmCall)).scalars().all()
    assert len(rows) == len(TRUTHS) == env.client.calls
    assert {r.model for r in rows} == {MODEL} and {r.prompt_version for r in rows} == {"v1"}
    assert all(r.status == "ok" and r.cost_usd_micros == 10_000 for r in rows)  # 1500 in + 700 out
    assert len({r.request_hash for r in rows}) == len(rows)


def test_reprocessing_the_same_file_uses_the_cache(env: Env) -> None:
    truth = TRUTHS[1]
    invoice_id = by_file(env, truth)
    calls_before = env.client.calls
    with Session(env.engine) as s:
        rows_before = s.scalar(select(func.count()).select_from(LlmCall))
        # simulate a retry after a failure: back to `failed`, extraction rows gone
        s.execute(text("delete from field_extractions where invoice_id = :i"), {"i": invoice_id})
        s.execute(text("delete from invoice_lines where invoice_id = :i"), {"i": invoice_id})
        s.execute(update(Invoice).where(Invoice.id == invoice_id).values(status="failed"))
        s.commit()
        inv = s.get_one(Invoice, invoice_id)
        doc = s.get_one(Document, inv.document_id)
        extract_invoice(s, env.storage, env.settings, env.client, BankVault(BANK_KEY), inv.id)
        s.commit()
        assert inv.status == "extracted"
        assert s.scalar(select(func.count()).select_from(LlmCall)) == rows_before  # no new row
        n_fields = select(func.count()).select_from(FieldExtraction)
        n_fields = n_fields.where(FieldExtraction.invoice_id == inv.id)
        assert s.scalar(n_fields) == 13 + 6 * len(truth["lines"])
        assert doc.file_sha256
    assert env.client.calls == calls_before  # the model was not asked again


def test_running_the_stage_twice_changes_nothing(env: Env) -> None:
    truth = TRUTHS[2]
    invoice_id = by_file(env, truth)
    with Session(env.engine) as s:
        before = s.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.invoice_id == invoice_id)
        )
        extract_invoice(s, env.storage, env.settings, env.client, BankVault(BANK_KEY), invoice_id)
        s.commit()
        after = s.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.invoice_id == invoice_id)
        )
    assert before == after


# ---- spend cap and configuration -----------------------------------------------------------


def fresh_tenant_env(
    migrated_db_url: str, tmp_path: Path, **kw: Any
) -> tuple[Engine, Settings, LocalStorage, Path]:
    settings = make_settings(migrated_db_url, tmp_path, default_tenant_id=str(uuid.uuid4()), **kw)
    return (
        create_engine(migrated_db_url),
        settings,
        LocalStorage(settings.storage_dir),
        tmp_path / "fx",
    )


def test_hitting_the_daily_cap_pauses_jobs_and_shows_it_in_the_api(
    migrated_db_url: str, tmp_path: Path
) -> None:
    engine, settings, storage, fx = fresh_tenant_env(
        migrated_db_url, tmp_path, daily_spend_cap_usd=Decimal("0")
    )
    truth = TRUTHS[3]
    ids = ingest_all(engine, settings, storage, fx, [truth])
    client = RecordedClient(model=MODEL, directory=fx, price=PRICE)
    drain(engine, storage, settings, client)
    assert client.calls == 0  # the cap stopped the call
    with TestClient(create_app(settings)) as api:
        status = api.get("/extraction/status", headers=AUTH).json()
        assert status["cap_reached"] is True and status["paused_jobs"] == 1
        assert status["resumes_at"] is not None
        assert api.get("/jobs", headers=AUTH).json()["paused"] == 1
    with Session(engine) as s:
        inv = s.get_one(Invoice, ids[truth["file"]])
        assert inv.status == "extracting"  # visible as in progress, not silently lost
        job = s.execute(
            select(Job).where(
                Job.type == EXTRACT_JOB, Job.payload["invoice_id"].astext == str(inv.id)
            )
        ).scalar_one()
        assert (job.status, job.last_error, job.attempts) == ("queued", "SPEND_CAP_REACHED", 0)

        # raising the cap and letting the pause elapse resumes the same job
        s.execute(update(Job).where(Job.id == job.id).values(run_after=func.now()))
        s.commit()
    raised = settings.model_copy(update={"daily_spend_cap_usd": Decimal("5")})
    drain(engine, storage, raised, client)
    with Session(engine) as s:
        assert s.get_one(Invoice, ids[truth["file"]]).status == "extracted"
        paused = select(func.count()).select_from(AuditEvent).where(
            AuditEvent.invoice_id == ids[truth["file"]],
            AuditEvent.event_type == "extraction_paused",
        )  # fmt: skip
        assert s.scalar(paused) == 1  # one audit event per pause, not one per poll
    engine.dispose()


def test_without_an_api_key_extraction_is_paused_not_failed(
    migrated_db_url: str, tmp_path: Path
) -> None:
    engine, settings, storage, _ = fresh_tenant_env(migrated_db_url, tmp_path)
    truth = TRUTHS[4]
    ids = ingest_all(engine, settings, storage, tmp_path / "fx", [truth])
    while run_once(engine, storage, settings):  # the real handlers: build_client finds no key
        pass
    with Session(engine) as s:
        assert s.get_one(Invoice, ids[truth["file"]]).status == "received"
        job = s.execute(
            select(Job).where(
                Job.type == EXTRACT_JOB, Job.payload["invoice_id"].astext == str(ids[truth["file"]])
            )
        ).scalar_one()
        assert (job.status, job.last_error) == ("queued", "EXTRACTION_NOT_CONFIGURED")
    with TestClient(create_app(settings)) as api:
        assert api.get("/extraction/status", headers=AUTH).json()["configured"] is False
    engine.dispose()
