"""The evaluation harness (playbook §8, M10): run the real pipeline over the golden set and report
honest accuracy, split by field and by document quality.

  DATABASE_URL=... STORAGE_DIR=... uv run --project apps/api python eval/run_eval.py

Uses whatever model backend is already configured (LLM_PROVIDER / EXTRACTION_MODEL / the matching
API key in .env) - model choice is configuration, not code (playbook §3). A non-Anthropic run is
clearly labelled in the report as a non-reference build (ADR 0010); it still exercises the same
pipeline and metrics.

Resets the pipeline tables first (same as `scripts/load-demo-pipeline.py`): point DATABASE_URL at
a database you don't mind resetting, such as the local Compose `db` service, not anything with data
you care about.

Options (env vars):
  SUBSET=N              only the first N golden invoices (a quick, cheaper check)
  RECORDED_DIR=<dir>    replay real, previously-captured answers from here instead of calling the
                        model (used by CI: free and deterministic; requires LLM_PROVIDER=recorded)
"""

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from cryptography.fernet import Fernet  # noqa: E402
from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from intake.config import Settings  # noqa: E402
from intake.core.eval_metrics import InvoiceEval, aggregate, compare_invoice  # noqa: E402
from intake.core.extraction import FieldResult, Interpreted, LineResult  # noqa: E402
from intake.core.normalize import normalize_bank_account  # noqa: E402
from intake.core.statuses import DocQuality, Route  # noqa: E402
from intake.db.models import (  # noqa: E402
    EvalRun,
    FieldExtraction,
    Invoice,
    InvoiceException,
    InvoiceLine,
    Job,
    LlmCall,
    Tenant,
)
from intake.eval_report import RunInfo, render_report  # noqa: E402
from intake.extract.factory import build_client  # noqa: E402
from intake.extract.pipeline import EXTRACT_JOB  # noqa: E402
from intake.extract.recorded import RecordedClient  # noqa: E402
from intake.ingest.service import UploadIngestor  # noqa: E402
from intake.ingest.storage import LocalStorage  # noqa: E402
from intake.security import BankVault  # noqa: E402
from intake.seed import DEFAULT_SEED_DIR, load_master  # noqa: E402
from intake.worker.handlers import HANDLERS, make_extract_handler  # noqa: E402
from intake.worker.runner import run_once  # noqa: E402

GOLDEN_DIR = ROOT / "data" / "golden"
REPORTS_DIR = ROOT / "eval" / "reports"
PIPELINE_TABLES = (
    "exceptions", "check_results",
    "jobs", "llm_calls", "field_extractions", "invoice_lines", "invoices", "documents",
)  # fmt: skip
_BANK_FIELD = "supplier_bank_account"


def load_golden_truths() -> list[dict[str, Any]]:
    paths = sorted((GOLDEN_DIR / "truth").glob("*.json"))
    truths = [json.loads(p.read_text()) for p in paths]
    return [t for t in truths if t["readable"]]


def drain(
    engine: Any, storage: Any, settings: Settings, handlers: dict[str, Any], timeout_s: float
) -> None:
    """Run every queued job to a terminal state, waiting out a transient failure's backoff instead
    of giving up the moment nothing is immediately claimable (`run_once` alone stops there)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if run_once(engine, storage, settings, handlers):
            continue
        with Session(engine) as s:
            pending = s.execute(
                select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))
            ).scalar_one()
        if pending == 0:
            return
        time.sleep(2)
    raise TimeoutError(f"jobs still pending after {timeout_s:.0f}s")


def git_sha() -> str:
    try:
        return subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=True,
        ).stdout.strip()  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_interpreted(
    session: Session, tenant_id: uuid.UUID, inv: Invoice, vault: Any
) -> Interpreted:
    lines = [
        LineResult(
            ln.line_no,
            ln.description,
            ln.sku,
            ln.qty,
            ln.unit_price_minor,
            ln.amount_minor,
            ln.tax_rate,
        )  # fmt: skip
        for ln in session.execute(
            select(InvoiceLine)
            .where(InvoiceLine.tenant_id == tenant_id, InvoiceLine.invoice_id == inv.id)
            .order_by(InvoiceLine.line_no)
        ).scalars()
    ]
    fields = []
    for f in session.execute(
        select(FieldExtraction).where(
            FieldExtraction.tenant_id == tenant_id, FieldExtraction.invoice_id == inv.id
        )
    ).scalars():
        normalized = f.normalized_value
        if f.field == _BANK_FIELD and f.raw_value:
            # Stored normalized_value is a keyed hash (never the account, even here); decrypt the
            # sealed raw value to compare against the golden truth's plain account number.
            normalized = normalize_bank_account(vault.decrypt(f.raw_value))
        fields.append(FieldResult(f.field, f.raw_value, normalized, f.confidence or 0, {}, f.page))
    return Interpreted(
        supplier_name=inv.supplier_name, invoice_number=inv.invoice_number,
        po_number=inv.po_number, payment_terms=inv.payment_terms, invoice_date=inv.invoice_date,
        due_date=inv.due_date, currency=inv.currency, subtotal_minor=inv.subtotal_minor,
        tax_minor=inv.tax_minor, total_minor=inv.total_minor, lines=lines, fields=fields,
    )  # fmt: skip


def truth_for_compare(truth: dict[str, Any]) -> dict[str, Any]:
    """A copy with the bank account normalized the same way the actual value is, for a fair
    comparison (see build_interpreted)."""
    account = truth["header"].get(_BANK_FIELD)
    if account is None:
        return truth
    header = {**truth["header"], _BANK_FIELD: normalize_bank_account(account)}
    return {**truth, "header": header}


def evaluate_invoice(
    session: Session, tenant_id: uuid.UUID, inv: Invoice, truth: dict[str, Any], vault: Any
) -> InvoiceEval:
    interpreted = build_interpreted(session, tenant_id, inv, vault)
    raised = list(
        session.execute(
            select(InvoiceException.code).where(
                InvoiceException.tenant_id == tenant_id, InvoiceException.invoice_id == inv.id
            )
        ).scalars()
    )
    cost = (
        session.execute(
            select(LlmCall.cost_usd_micros).where(
                LlmCall.tenant_id == tenant_id, LlmCall.invoice_id == inv.id
            )
        )
        .scalars()
        .all()
    )
    latency_ms = int((inv.updated_at - inv.created_at).total_seconds() * 1000)
    return compare_invoice(
        doc_quality=DocQuality(truth["doc_quality"]),
        interpreted=interpreted,
        truth=truth_for_compare(truth),
        raised_codes=raised,
        routed_straight_through=inv.route == Route.STRAIGHT_THROUGH.value,
        cost_usd_micros=sum(cost),
        latency_ms=latency_ms,
    )


def main() -> None:
    database_url = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://intake:intake@localhost:5432/intake"
    )
    storage_dir = os.environ.get("STORAGE_DIR", "./storage")
    subset = int(os.environ["SUBSET"]) if os.environ.get("SUBSET") else None
    recorded_dir = os.environ.get("RECORDED_DIR")

    master = json.loads((DEFAULT_SEED_DIR / "master.json").read_text())
    tenant = master["tenant"]
    truths = load_golden_truths()
    if subset:
        truths = truths[:subset]

    bank_key = os.environ.get("BANK_ENCRYPTION_KEY") or Fernet.generate_key().decode()
    settings = Settings(
        database_url=database_url, storage_dir=storage_dir,
        default_tenant_id=tenant["id"], bank_encryption_key=bank_key,
        # A live backend can ask for a longer wait than the app's own defaults assume (observed:
        # OpenRouter's 402 "in-flight budget" suggests retrying after 120s) - more attempts and a
        # longer base than the interactive-worker defaults, so a transient condition gets a real
        # chance to clear during an unattended eval run.
        job_max_attempts=6, job_backoff_base_s=30,
    )  # fmt: skip
    vault = BankVault(settings.bank_encryption_key)
    if recorded_dir:
        client = RecordedClient(model=settings.extraction_model, directory=Path(recorded_dir))
        handlers = {**HANDLERS, EXTRACT_JOB: make_extract_handler(lambda _: client)}
    else:
        if build_client(settings) is None:
            sys.exit(
                "No model backend is configured (LLM_PROVIDER / EXTRACTION_MODEL / the matching "
                "API key). Set them in .env, or set RECORDED_DIR to replay saved answers."
            )
        handlers = HANDLERS

    engine = create_engine(database_url)
    with engine.begin() as conn:
        for t in PIPELINE_TABLES:
            conn.execute(text(f"delete from {t}"))  # noqa: S608
    storage = LocalStorage(settings.storage_dir)
    with Session(engine) as s:
        s.merge(
            Tenant(id=uuid.UUID(tenant["id"]), name=tenant["name"], settings=tenant["settings"])
        )
        s.commit()
        load_master(s, master, vault)
        s.commit()

    tenant_id = uuid.UUID(tenant["id"])
    ingestor = UploadIngestor(settings, storage)
    invoice_ids: dict[str, uuid.UUID] = {}
    for truth in truths:
        path = GOLDEN_DIR / "invoices" / truth["file"]
        with Session(engine) as s:
            res = ingestor.ingest(s, content=path.read_bytes(), filename=path.name, source="eval")
            s.commit()
        invoice_ids[truth["id"]] = res.invoice_id

    print(f"ingested {len(truths)} golden invoices; running the pipeline...")
    drain(engine, storage, settings, handlers, timeout_s=1800)

    evals: list[InvoiceEval] = []
    with Session(engine) as s:
        for truth in truths:
            inv = s.get_one(Invoice, invoice_ids[truth["id"]])
            evals.append(evaluate_invoice(s, tenant_id, inv, truth, vault))

    metrics = aggregate(evals)
    now = datetime.now(UTC)
    run_info = RunInfo(
        provider=settings.llm_provider, model=settings.extraction_model,
        prompt_version=settings.extraction_prompt_version, git_sha=git_sha(), generated_at=now,
    )  # fmt: skip
    report = render_report(metrics, run_info)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{now:%Y-%m-%d}-{run_info.git_sha}.md"
    report_path.write_text(report, encoding="utf-8")
    with Session(engine) as s:
        s.add(
            EvalRun(
                git_sha=run_info.git_sha, model=run_info.model,
                prompt_version=run_info.prompt_version,
                metrics={
                    "invoice_count": metrics.invoice_count,
                    "provider": run_info.provider,
                    "false_clear_rate": metrics.false_clear_rate,
                    "false_clears": metrics.false_clears,
                    "touchless_rate": metrics.touchless_rate,
                    "exception_recall": metrics.exception_recall,
                    "exception_precision": metrics.exception_precision,
                    "line_f1": metrics.lines.f1,
                    "mean_cost_usd_micros": metrics.mean_cost_usd_micros,
                    "latency_p50_ms": metrics.latency_p50_ms,
                    "latency_p95_ms": metrics.latency_p95_ms,
                },
            )
        )  # fmt: skip
        s.commit()

    print(report)
    print(f"\nwrote {report_path}")
    if metrics.false_clears > 0:
        sys.exit(1)  # the regression gate: a real full run with any false clear fails the command


if __name__ == "__main__":
    main()
