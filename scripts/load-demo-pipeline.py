"""Load the demo world into a database by running the real pipeline with recorded model answers.

Dev and test tool: it reuses the test helpers, so every seed invoice is uploaded, read (from recorded
answers built from the answer key, never a live model call), checked, matched and routed, exactly as
in the acceptance tests. Use it to look at the review screens with real data.

  DATABASE_URL=... STORAGE_DIR=... uv run --project apps/api python scripts/load-demo-pipeline.py

The API must run with the same DATABASE_URL, STORAGE_DIR, BANK_ENCRYPTION_KEY and VALIDATION_TODAY.
"""

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from intake.checks.duplicates import DEDUPE_JOB  # noqa: E402
from intake.checks.matching import MATCH_JOB  # noqa: E402
from intake.checks.pipeline import VALIDATE_JOB  # noqa: E402
from intake.checks.routing import ROUTE_JOB  # noqa: E402
from intake.db.models import Tenant  # noqa: E402
from intake.extract.pipeline import EXTRACT_JOB  # noqa: E402
from intake.extract.recorded import RecordedClient  # noqa: E402
from intake.ingest.service import PROCESS_JOB  # noqa: E402
from intake.ingest.storage import LocalStorage  # noqa: E402
from intake.security import BankVault  # noqa: E402
from intake.seed import load_master  # noqa: E402
from intake.worker.handlers import HANDLERS, make_extract_handler  # noqa: E402
from intake.worker.runner import run_once  # noqa: E402
from tests.integration.test_extraction_pipeline import (  # noqa: E402
    BANK_KEY,
    MASTER,
    MODEL,
    PRICE,
    ingest_all,
    make_settings,
    purge,
)
from tests.support.truth_payload import load_truths  # noqa: E402

url = os.environ["DATABASE_URL"]
storage_dir = os.environ["STORAGE_DIR"]
tenant = MASTER["tenant"]
truths = load_truths(None)

engine = create_engine(url)
settings = make_settings(
    url, Path(storage_dir).parent, storage_dir=storage_dir, default_tenant_id=tenant["id"],
    validation_today=date(2026, 9, 25),
)  # fmt: skip
storage = LocalStorage(settings.storage_dir)
purge(engine)
with Session(engine) as s:
    s.merge(Tenant(id=tenant["id"], name=tenant["name"], settings=tenant["settings"]))
    s.commit()
    load_master(s, MASTER, BankVault(BANK_KEY))
    s.commit()
fixtures = Path(tempfile.mkdtemp(prefix="fixtures-"))
ingest_all(engine, settings, storage, fixtures, truths)
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
print(f"loaded {len(truths)} invoices; bank key for the API: {BANK_KEY}")
