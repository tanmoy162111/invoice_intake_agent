"""M4 acceptance: every planted math, date, supplier, currency, tax and bank problem in the seed
data is caught, end to end (real worker, real database, recorded model answers), and invoices that
should clear raise no validation failure."""

import json
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session

from intake.checks.duplicates import DEDUPE_JOB
from intake.checks.pipeline import VALIDATE_JOB, validate_invoice
from intake.core.validate import CheckCode
from intake.db.models import AuditEvent, CheckResult, Invoice, Tenant
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

TRUTHS = load_truths(None)  # every readable invoice, clean or not
TODAY = date(2026, 9, 25)
M4_CODES = {c.value for c in CheckCode}


class Env:
    def __init__(
        self, engine: Engine, settings: Any, storage: LocalStorage, ids: dict[str, uuid.UUID]
    ):
        self.engine, self.settings, self.storage, self.ids = engine, settings, storage, ids


@pytest.fixture(scope="module")
def env(migrated_db_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Env]:
    tmp = tmp_path_factory.mktemp("m4")
    engine = create_engine(migrated_db_url)
    purge(engine)
    settings = make_settings(
        migrated_db_url, tmp, default_tenant_id=MASTER["tenant"]["id"], validation_today=TODAY
    )
    storage = LocalStorage(settings.storage_dir)
    with Session(engine) as s:
        s.merge(Tenant(id=uuid.UUID(MASTER["tenant"]["id"]), name=MASTER["tenant"]["name"],
                       settings=MASTER["tenant"]["settings"]))  # fmt: skip
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
        DEDUPE_JOB: lambda *_: None,  # M4 tests stop after the checks; M5 has its own tests
    }
    while run_once(engine, storage, settings, handlers):
        pass
    yield Env(engine, settings, storage, ids)
    with engine.begin() as conn:
        for t in PIPELINE_TABLES:
            conn.execute(text(f"delete from {t}"))  # noqa: S608
        for t in ("receipt_lines", "goods_receipts", "po_lines", "purchase_orders", "suppliers"):
            conn.execute(
                text(f"delete from {t} where tenant_id = :t"), {"t": MASTER["tenant"]["id"]}
            )
    engine.dispose()


def outcomes(env: Env, truth: dict[str, Any]) -> dict[str, str]:
    with Session(env.engine) as s:
        rows = s.execute(
            select(CheckResult).where(CheckResult.invoice_id == env.ids[truth["file"]])
        ).scalars()
        return {r.check_code: r.details["outcome"] for r in rows}


def test_every_readable_seed_invoice_was_extracted_and_checked(env: Env) -> None:
    assert len(TRUTHS) == 118
    with Session(env.engine) as s:
        for truth in TRUTHS:
            inv = s.get_one(Invoice, env.ids[truth["file"]])
            assert inv.status == "checking", (truth["file"], inv.status)
            results = s.execute(
                select(CheckResult).where(CheckResult.invoice_id == inv.id)
            ).scalars().all()  # fmt: skip
            assert {r.check_code for r in results} == M4_CODES and len(results) == 7
            assert {r.rule_version for r in results} == {"v1"}
            assert all(r.passed == (r.details["outcome"] == "pass") for r in results)


def test_every_planted_validation_problem_is_caught(env: Env) -> None:
    missed: dict[str, set[str]] = {}
    planted = 0
    for truth in TRUTHS:
        want = set(truth["expected"]["must_raise"]) & M4_CODES
        planted += len(want)
        got = {c for c, o in outcomes(env, truth).items() if o == "fail"}
        if want - got:
            missed[truth["id"]] = want - got
    assert planted >= 20  # 4 line math, 4 totals, 3 tax, 3 dates, 3 currency, 3 supplier, 1 bank
    assert missed == {}


def test_invoices_that_should_clear_raise_no_validation_failure(env: Env) -> None:
    should_clear = [t for t in TRUTHS if t["expected"]["should_clear"]]
    assert len(should_clear) >= 30
    false_alarms = {
        t["id"]: sorted(c for c, o in outcomes(env, t).items() if o == "fail")
        for t in should_clear
        if any(o == "fail" for o in outcomes(env, t).values())
    }
    assert false_alarms == {}


def test_no_validation_failure_beyond_what_the_seed_expects(env: Env) -> None:
    unexpected: dict[str, list[str]] = {}
    for truth in TRUTHS:
        allowed = set(truth["expected"]["must_raise"]) | set(truth["expected"]["may_raise"])
        if "*" in allowed:
            continue
        extra = sorted(
            c for c, o in outcomes(env, truth).items() if o == "fail" and c not in allowed
        )
        if extra:
            unexpected[truth["id"]] = extra
    assert unexpected == {}


def test_known_suppliers_are_linked_and_unknown_ones_are_not(env: Env) -> None:
    with Session(env.engine) as s:
        for truth in TRUTHS:
            inv = s.get_one(Invoice, env.ids[truth["file"]])
            assert (inv.supplier_id is not None) == truth["supplier_on_file"], truth["id"]


def test_the_unknown_supplier_decoys_report_their_closest_match(env: Env) -> None:
    decoys = [t for t in TRUTHS if "UNKNOWN_SUPPLIER" in t["expected"]["must_raise"]]
    assert len(decoys) == 3
    with Session(env.engine) as s:
        for truth in decoys:
            r = s.execute(
                select(CheckResult).where(
                    CheckResult.invoice_id == env.ids[truth["file"]],
                    CheckResult.check_code == "UNKNOWN_SUPPLIER",
                )
            ).scalar_one()
            assert (
                r.details["closest_name"]
                and r.details["printed_name"] == truth["header"]["supplier_name"]
            )


def test_the_changed_bank_account_is_caught_and_never_stored_in_the_clear(env: Env) -> None:
    truth = next(t for t in TRUTHS if "BANK_DETAILS_CHANGED" in t["expected"]["must_raise"])
    digits = "".join(ch for ch in truth["header"]["supplier_bank_account"] if ch.isdigit())
    with Session(env.engine) as s:
        for table, column in (("check_results", "details"), ("audit_events", "data")):
            hits = s.execute(
                text(f"select count(*) from {table} where {column}::text like :p"),  # noqa: S608
                {"p": f"%{digits[-8:]}%"},
            ).scalar_one()
            assert hits == 0
    assert outcomes(env, truth)["BANK_DETAILS_CHANGED"] == "fail"


def test_each_invoice_audits_its_checks_once_with_codes_only(env: Env) -> None:
    truth = next(t for t in TRUTHS if "TOTAL_MISMATCH" in t["expected"]["must_raise"])
    with Session(env.engine) as s:
        events = s.execute(
            select(AuditEvent).where(
                AuditEvent.invoice_id == env.ids[truth["file"]],
                AuditEvent.event_type == "checks_completed",
            )
        ).scalars().all()  # fmt: skip
        assert len(events) == 1
        data = events[0].data
        assert "TOTAL_MISMATCH" in data["failed"] and data["rule_version"] == "v1"
        assert set(data) == {
            "rule_version", "passed", "failed", "skipped", "supplier_matched_by",
            "as_of", "as_of_overridden",
        }  # fmt: skip
        assert (data["as_of"], data["as_of_overridden"]) == (TODAY.isoformat(), True)


def test_running_the_stage_again_changes_nothing(env: Env) -> None:
    truth = TRUTHS[3]
    invoice_id = env.ids[truth["file"]]
    with Session(env.engine) as s:
        before = s.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.invoice_id == invoice_id)
        )
        validate_invoice(s, env.settings, invoice_id, tenant_id=uuid.UUID(MASTER["tenant"]["id"]))
        s.commit()
        after = s.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.invoice_id == invoice_id)
        )
        n = s.scalar(
            select(func.count())
            .select_from(CheckResult)
            .where(CheckResult.invoice_id == invoice_id)
        )
    assert before == after and n == 7


def test_json_details_carry_the_numbers_for_an_explanation(env: Env) -> None:
    truth = next(t for t in TRUTHS if "LINE_MATH_MISMATCH" in t["expected"]["must_raise"])
    with Session(env.engine) as s:
        r = s.execute(
            select(CheckResult).where(
                CheckResult.invoice_id == env.ids[truth["file"]],
                CheckResult.check_code == "LINE_MATH_MISMATCH",
            )
        ).scalar_one()
    bad = r.details["lines"][0]
    assert set(bad) == {"line_no", "quantity", "unit_price_minor", "expected_minor", "amount_minor"}
    assert bad["expected_minor"] != bad["amount_minor"]
    json.dumps(r.details)


def test_the_stage_refuses_an_invoice_that_belongs_to_another_tenant(env: Env) -> None:
    from intake.checks.pipeline import WrongTenant

    truth = TRUTHS[5]
    with Session(env.engine) as s, pytest.raises(WrongTenant):
        validate_invoice(s, env.settings, env.ids[truth["file"]], tenant_id=uuid.uuid4())


def test_a_result_cannot_be_recorded_twice_for_the_same_rule_version(env: Env) -> None:
    from sqlalchemy.exc import IntegrityError

    truth = TRUTHS[6]
    with Session(env.engine) as s:
        inv = s.get_one(Invoice, env.ids[truth["file"]])
        s.add(
            CheckResult(
                tenant_id=inv.tenant_id, invoice_id=inv.id, check_code="TOTAL_MISMATCH",
                passed=True, details={}, rule_version="v1",
            )
        )  # fmt: skip
        with pytest.raises(IntegrityError):
            s.commit()


def test_an_extracted_but_unread_bank_account_is_not_a_pass(env: Env) -> None:
    # a supplier layout that prints no bank: the model was certain, so nothing changed
    no_bank = [t for t in TRUTHS if t["header"]["supplier_bank_account"] is None]
    assert no_bank
    assert all(outcomes(env, t)["BANK_DETAILS_CHANGED"] == "pass" for t in no_bank[:5])
