"""M6 acceptance: every planted PO, price, quantity, receipt and over-billing problem in the seed
data is detected, and nothing else is flagged, end to end (real worker and database, recorded
answers)."""

import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session

from intake.checks.duplicates import DEDUPE_JOB
from intake.checks.matching import MATCH_JOB, WAITING, match_invoice
from intake.checks.pipeline import VALIDATE_JOB, WrongTenant
from intake.core.match import MatchCode
from intake.db.models import AuditEvent, CheckResult, Invoice, InvoiceLine, Tenant
from intake.extract.pipeline import EXTRACT_JOB
from intake.extract.recorded import RecordedClient
from intake.ingest.service import PROCESS_JOB
from intake.ingest.storage import LocalStorage
from intake.security import BankVault
from intake.seed import load_master
from intake.worker.handlers import HANDLERS, make_extract_handler
from intake.worker.runner import run_once
from tests.integration.test_extraction_pipeline import (
    BANK_KEY,
    MASTER,
    MODEL,
    PIPELINE_TABLES,
    PRICE,
    ingest_all,
    make_settings,
    purge,
)
from tests.support.truth_payload import load_truths

TRUTHS = load_truths(None)
TODAY = date(2026, 9, 25)
TENANT = uuid.UUID(MASTER["tenant"]["id"])
CODES = {c.value for c in MatchCode}
# A digit transposition planted in a line amount (LINE_MATH_MISMATCH) carries into the printed
# subtotal, so these invoices really do bill more than their PO. The seed's `may_raise` does not
# list that knock-on, and the fixed golden set is not edited here.
KNOCK_ON = {"inv-004", "inv-064", "inv-075", "inv-077"}


class Env:
    def __init__(self, engine: Engine, settings: Any, ids: dict[str, uuid.UUID]) -> None:
        self.engine, self.settings, self.ids = engine, settings, ids
        self.by_invoice = {v: k for k, v in ids.items()}
        self.truth_of = {t["file"]: t for t in TRUTHS}


@pytest.fixture(scope="module")
def env(migrated_db_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Env]:
    tmp = tmp_path_factory.mktemp("m6")
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
    ids = ingest_all(engine, settings, storage, fixtures, TRUTHS)
    client = RecordedClient(model=MODEL, directory=fixtures, price=PRICE)
    handlers = {
        PROCESS_JOB: HANDLERS[PROCESS_JOB],
        EXTRACT_JOB: make_extract_handler(lambda _: client),
        VALIDATE_JOB: HANDLERS[VALIDATE_JOB],
        DEDUPE_JOB: HANDLERS[DEDUPE_JOB],
        MATCH_JOB: HANDLERS[MATCH_JOB],
    }
    while run_once(engine, storage, settings, handlers):
        pass
    yield Env(engine, settings, ids)
    with engine.begin() as conn:
        for t in PIPELINE_TABLES:
            conn.execute(text(f"delete from {t}"))  # noqa: S608
        for t in ("receipt_lines", "goods_receipts", "po_lines", "purchase_orders", "suppliers"):
            conn.execute(text(f"delete from {t} where tenant_id = :t"), {"t": str(TENANT)})
    engine.dispose()


def rows_by_truth(env: Env) -> dict[str, dict[str, CheckResult]]:
    """truth id -> match code -> stored result."""
    files = {t["file"]: t["id"] for t in TRUTHS}
    out: dict[str, dict[str, CheckResult]] = {}
    with Session(env.engine) as s:
        for r in s.execute(select(CheckResult).where(CheckResult.check_code.in_(CODES))).scalars():
            if r.invoice_id in env.by_invoice:
                out.setdefault(files[env.by_invoice[r.invoice_id]], {})[r.check_code] = r
    return out


def outcome(r: CheckResult) -> str:
    return str(r.details["outcome"])


def failed(rows: dict[str, CheckResult]) -> set[str]:
    return {code for code, r in rows.items() if outcome(r) == "fail"}


def test_every_readable_invoice_gets_one_result_per_match_check(env: Env) -> None:
    rows = rows_by_truth(env)
    assert len(TRUTHS) == 118 and len(rows) == 118
    assert all(set(r) == CODES for r in rows.values())
    assert {r.rule_version for per in rows.values() for r in per.values()} == {"v1"}
    assert all(r.passed == (outcome(r) == "pass") for per in rows.values() for r in per.values())


def test_every_planted_po_problem_is_detected(env: Env) -> None:
    rows = rows_by_truth(env)
    planted = [
        (t["id"], code) for t in TRUTHS for code in t["expected"]["must_raise"] if code in CODES
    ]
    assert len(planted) == 25
    missed = [(i, c) for i, c in planted if c not in failed(rows[i])]
    assert not missed, missed


def test_nothing_else_is_flagged(env: Env) -> None:
    rows = rows_by_truth(env)
    extra = {}
    for t in TRUTHS:
        allowed = set(t["expected"]["must_raise"]) | set(t["expected"]["may_raise"])
        if "*" in allowed:
            continue
        surplus = failed(rows[t["id"]]) - allowed
        if t["id"] in KNOCK_ON:
            assert surplus == {"PO_OVERBILLED"}
            continue
        if surplus:
            extra[t["id"]] = surplus
    assert not extra, extra


def test_no_invoice_that_should_clear_fails_or_is_left_unchecked(env: Env) -> None:
    rows = rows_by_truth(env)
    bad = {}
    for t in TRUTHS:
        if t["expected"]["should_clear"]:
            not_pass = {c: outcome(r) for c, r in rows[t["id"]].items() if outcome(r) != "pass"}
            if not_pass:
                bad[t["id"]] = not_pass
    assert not bad, bad


def test_an_invoice_without_a_po_number_fails_no_po_when_no_open_po_matches(env: Env) -> None:
    rows = rows_by_truth(env)
    for t in TRUTHS:
        if "NO_PO" in t["expected"]["must_raise"]:
            r = rows[t["id"]][MatchCode.NO_PO.value]
            assert outcome(r) == "fail" and r.details["reason"] == "NO_OPEN_PO_WITH_MATCHING_TOTAL"


def test_over_billing_counts_what_earlier_invoices_billed(env: Env) -> None:
    rows = rows_by_truth(env)
    d = rows["inv-095"][MatchCode.PO_OVERBILLED.value].details
    assert outcome(rows["inv-095"][MatchCode.PO_OVERBILLED.value]) == "fail"
    billed_before = d["billed_before_minor"]
    assert isinstance(billed_before, int) and billed_before > 0
    assert d["over_minor"] == d["billed_total_minor"] - d["po_total_minor"] > 0  # type: ignore[operator]


def test_missing_and_partial_receipts_are_told_apart(env: Env) -> None:
    rows = rows_by_truth(env)
    for i in ("inv-031", "inv-037", "inv-049", "inv-097"):
        assert outcome(rows[i][MatchCode.RECEIPT_MISSING.value]) == "fail"
        assert rows[i][MatchCode.QTY_NOT_RECEIVED.value].details["reason"] == "NO_RECEIPT"
    for i in ("inv-015", "inv-039", "inv-047", "inv-101"):
        assert outcome(rows[i][MatchCode.RECEIPT_MISSING.value]) == "pass"
        assert outcome(rows[i][MatchCode.QTY_NOT_RECEIVED.value]) == "fail"


def test_matched_lines_are_recorded_on_the_invoice(env: Env) -> None:
    with Session(env.engine) as s:
        matched = s.scalar(
            select(func.count()).select_from(InvoiceLine).where(
                InvoiceLine.matched_po_line_id.is_not(None),
                InvoiceLine.invoice_id.in_(list(env.by_invoice)),
            )
        )  # fmt: skip
    assert matched and matched > 100


def test_the_audit_trail_holds_ids_and_outcomes_only(env: Env) -> None:
    with Session(env.engine) as s:
        events = [
            e
            for e in s.execute(
                select(AuditEvent).where(AuditEvent.event_type == "match_completed")
            ).scalars()
            if e.invoice_id in env.by_invoice
        ]
    assert len(events) == 118
    for e in events:
        assert set(e.data) == {
            "rule_version",
            "po_id",
            "inferred",
            "outcomes",
            "line_matches",
            "pending_earlier",
        }
    blob = " ".join(str(e.data) for e in events)
    assert not any(t["header"]["invoice_number"] in blob for t in TRUTHS[:20])


def test_running_the_stage_again_changes_nothing(env: Env) -> None:
    invoice_id = env.ids[TRUTHS[10]["file"]]
    with Session(env.engine) as s:
        count = select(func.count()).select_from(CheckResult).where(
            CheckResult.invoice_id == invoice_id
        )  # fmt: skip
        events = select(func.count()).select_from(AuditEvent).where(
            AuditEvent.invoice_id == invoice_id
        )  # fmt: skip
        before = (s.scalar(count), s.scalar(events))
        assert match_invoice(s, env.settings, invoice_id, tenant_id=TENANT) is None
        s.commit()
        assert (s.scalar(count), s.scalar(events)) == before


def test_another_tenants_job_is_refused(env: Env) -> None:
    with Session(env.engine) as s, pytest.raises(WrongTenant):
        match_invoice(s, env.settings, env.ids[TRUTHS[0]["file"]], tenant_id=uuid.uuid4())


def test_an_invoice_not_yet_checked_is_left_alone(env: Env) -> None:
    invoice_id = env.ids[TRUTHS[3]["file"]]
    with Session(env.engine) as s:
        inv = s.get_one(Invoice, invoice_id)
        original = inv.status
        inv.status = "extracted"
        s.flush()
        assert match_invoice(s, env.settings, invoice_id, tenant_id=TENANT) is None
        s.rollback()
        assert s.get_one(Invoice, invoice_id).status == original


def test_the_stage_waits_for_earlier_invoices_that_are_not_matched_yet(env: Env) -> None:
    invoice_id = env.ids[TRUTHS[-1]["file"]]
    with Session(env.engine) as s:
        s.execute(
            text("delete from check_results where invoice_id = :i and check_code = 'NO_PO'"),
            {"i": str(invoice_id)},
        )
        first = env.ids[TRUTHS[0]["file"]]
        s.execute(
            text("delete from check_results where invoice_id = :i and check_code = 'NO_PO'"),
            {"i": str(first)},
        )
        s.execute(text("update invoices set status = 'checking' where id = :i"), {"i": str(first)})
        deferral = match_invoice(s, env.settings, invoice_id, tenant_id=TENANT)
        assert deferral is not None and deferral.reason == WAITING
        s.rollback()


def test_billing_from_an_older_rule_version_is_not_counted_again(env: Env) -> None:
    files = {t["id"]: t["file"] for t in TRUTHS}
    later, earlier = env.ids[files["inv-095"]], env.ids[files["inv-006"]]
    with Session(env.engine) as s:
        rows = {
            r.check_code: r
            for r in s.execute(
                select(CheckResult).where(CheckResult.invoice_id == earlier)
            ).scalars()
        }
        stale = rows[MatchCode.PO_OVERBILLED.value]
        s.add(
            CheckResult(
                tenant_id=TENANT, invoice_id=earlier, check_code=stale.check_code, passed=True,
                details={**stale.details, "billed_minor": 10**9}, rule_version="v0",
            )
        )  # fmt: skip
        s.execute(
            text("delete from check_results where invoice_id = :i and check_code = any(:c)"),
            {"i": str(later), "c": list(CODES)},
        )
        s.flush()
        before = stale.details["billed_minor"]
        assert match_invoice(s, env.settings, later, tenant_id=TENANT) is None
        fresh = s.execute(
            select(CheckResult).where(
                CheckResult.invoice_id == later,
                CheckResult.check_code == MatchCode.PO_OVERBILLED.value,
            )
        ).scalar_one()
        assert fresh.details["billed_before_minor"] == before
        s.rollback()
