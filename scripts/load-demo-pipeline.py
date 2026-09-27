"""Load the demo world into a database by running the real pipeline with recorded model answers.

Dev and test tool: it reuses the test helpers, so every seed invoice is uploaded, read (from recorded
answers built from the answer key, never a live model call), checked, matched and routed, exactly as
in the acceptance tests. Use it to look at the review screens with real data.

  DATABASE_URL=... STORAGE_DIR=... uv run --project apps/api python scripts/load-demo-pipeline.py

The API must run with the same DATABASE_URL, STORAGE_DIR, BANK_ENCRYPTION_KEY and VALIDATION_TODAY.

Optional, for the browser test (`make e2e`):
  BANK_ENCRYPTION_KEY  use this key instead of a random one (so the API can be started with it)
  FIXTURES_DIR         keep the recorded answers here (LLM_PROVIDER=recorded RECORDED_DIR=<same>)
  HOLD_OUT             a seed file name to record an answer for but not upload, so the test can upload it
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
from intake.extract.prompt import load_prompt  # noqa: E402
from intake.extract.recorded import RecordedClient, write_fixture  # noqa: E402
from intake.extract.service import cache_key  # noqa: E402
from intake.ingest.service import PROCESS_JOB  # noqa: E402
from intake.ingest.storage import LocalStorage  # noqa: E402
from intake.security import BankVault  # noqa: E402
from intake.seed import load_master  # noqa: E402
from intake.worker.handlers import HANDLERS, make_extract_handler  # noqa: E402
from intake.worker.runner import run_once  # noqa: E402
import tests.integration.test_extraction_pipeline as pipeline_helpers  # noqa: E402
from tests.integration.test_extraction_pipeline import (  # noqa: E402
    MASTER,
    MODEL,
    PRICE,
    ingest_all,
    make_settings,
    purge,
    res_sha,
)
from tests.support.truth_payload import load_truths, payload_from_truth, seed_file  # noqa: E402

# The helpers read this module-level key when they build settings and the vault.
BANK_KEY = os.environ.get("BANK_ENCRYPTION_KEY") or pipeline_helpers.BANK_KEY
pipeline_helpers.BANK_KEY = BANK_KEY

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
fixtures = Path(os.environ.get("FIXTURES_DIR") or tempfile.mkdtemp(prefix="fixtures-"))
hold_out = os.environ.get("HOLD_OUT", "")
held = [t for t in truths if t["file"] == hold_out] if hold_out else []
if hold_out and not held:
    sys.exit(f"HOLD_OUT {hold_out!r} is not a seed invoice")
ingest_all(engine, settings, storage, fixtures, [t for t in truths if t not in held])
for truth in held:  # answered but not uploaded: the browser test uploads it
    version = settings.extraction_prompt_version
    key = cache_key(res_sha(seed_file(truth)), MODEL, version, load_prompt(version))
    write_fixture(fixtures, key, payload_from_truth(truth))
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
print(f"loaded {len(truths) - len(held)} invoices; bank key for the API: {BANK_KEY}")
