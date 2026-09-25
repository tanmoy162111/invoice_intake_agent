"""M7 acceptance: every planted problem becomes an exception with a plain-language explanation that
carries the real numbers, and every invoice is routed. No invoice with a planted problem is ever
cleared (false clear rate 0%). End to end: real worker and database, recorded answers."""

import re
import uuid
from collections import defaultdict
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session

from intake.checks.duplicates import DEDUPE_JOB
from intake.checks.matching import MATCH_JOB
from intake.checks.pipeline import VALIDATE_JOB, WrongTenant
from intake.checks.routing import ROUTE_JOB, route_invoice
from intake.core.exceptions import SPECS, ExceptionCode, Severity
from intake.core.money import Money, format_money
from intake.core.statuses import InvoiceStatus, Route
from intake.db.invoices import fail_extraction
from intake.db.models import AuditEvent, Invoice, InvoiceException, Tenant
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
BY_ID = {t["id"]: t for t in TRUTHS}
# Codes that can only arise when a document cannot be read at all: those invoices fail extraction
# and never reach the checks, so the two unreadable seed documents are not in TRUTHS.
NOT_FROM_CHECKS = {"UNREADABLE_DOCUMENT"}


class Env:
    def __init__(self, engine: Engine, settings: Any, ids: dict[str, uuid.UUID]) -> None:
        self.engine, self.settings, self.ids = engine, settings, ids
        self.truth_id = {v: BY_ID_FILE[k] for k, v in ids.items()}


BY_ID_FILE = {t["file"]: t["id"] for t in TRUTHS}


@pytest.fixture(scope="module")
def env(migrated_db_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Env]:
    tmp = tmp_path_factory.mktemp("m7")
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
        ROUTE_JOB: HANDLERS[ROUTE_JOB],
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


def invoices(env: Env) -> dict[str, Invoice]:
    with Session(env.engine) as s:
        rows = s.execute(select(Invoice).where(Invoice.id.in_(list(env.truth_id)))).scalars().all()
        s.expunge_all()
    return {env.truth_id[r.id]: r for r in rows}


def exceptions(env: Env) -> dict[str, list[InvoiceException]]:
    out: dict[str, list[InvoiceException]] = defaultdict(list)
    with Session(env.engine) as s:
        rows = s.execute(
            select(InvoiceException).where(InvoiceException.invoice_id.in_(list(env.truth_id)))
        ).scalars()
        for r in rows:
            out[env.truth_id[r.invoice_id]].append(r)
        s.expunge_all()
    return out


def test_every_readable_invoice_is_routed(env: Env) -> None:
    inv = invoices(env)
    assert len(inv) == 118
    for i, row in inv.items():
        assert InvoiceStatus(row.status) in (InvoiceStatus.CLEARED, InvoiceStatus.NEEDS_REVIEW), i
        expected = Route.STRAIGHT_THROUGH if row.status == "cleared" else Route.REVIEW
        assert row.route == expected.value, i


def test_every_planted_problem_becomes_an_exception(env: Env) -> None:
    exc = exceptions(env)
    planted = [
        (t["id"], code)
        for t in TRUTHS
        for code in t["expected"]["must_raise"]
        if code not in NOT_FROM_CHECKS
    ]
    assert len(planted) == 58
    missing = [(i, c) for i, c in planted if c not in {e.code for e in exc[i]}]
    assert not missing, missing


def test_no_invoice_with_a_planted_problem_is_ever_cleared(env: Env) -> None:
    inv = invoices(env)
    false_clears = [
        i for i, t in BY_ID.items() if t["expected"]["must_raise"] and inv[i].status == "cleared"
    ]
    assert false_clears == []


def test_clearable_invoices_are_held_only_for_a_stated_reason(env: Env) -> None:
    """A clearable invoice may be held for exactly two reasons, both the "uncertain means human"
    rule: (1) a duplicate check that could not be completed (a similar earlier invoice has no
    readable currency); (2) a scanned or photographed document whose invoice number has no text
    layer to confirm it (75% confidence, below the 80% minimum). Nothing else."""
    inv, exc = invoices(env), exceptions(env)
    held = [
        i for i, t in BY_ID.items() if t["expected"]["should_clear"] and inv[i].status != "cleared"
    ]
    assert held
    for i in held:
        for e in exc[i]:
            if e.code == "POSSIBLE_DUPLICATE":
                assert e.severity == "review" and "could not be checked" in e.explanation, i
            else:
                assert e.code == "LOW_CONFIDENCE_FIELD", (i, e.code)
                assert BY_ID[i]["doc_quality"] in ("scanned", "photo"), i
                assert (
                    "invoice number" in e.explanation and "below the 80% minimum" in e.explanation
                )
    clean = [i for i in held if BY_ID[i]["doc_quality"] == "clean"]
    assert clean == ["inv-110"]  # the one clean PDF beside an earlier invoice with no currency


def test_a_cleared_invoice_has_no_open_exception(env: Env) -> None:
    inv, exc = invoices(env), exceptions(env)
    for i, row in inv.items():
        if row.status == "cleared":
            assert exc[i] == [], i


def test_blocks_are_blocks_and_severities_come_from_the_taxonomy(env: Env) -> None:
    for i, rows in exceptions(env).items():
        for e in rows:
            spec = SPECS[ExceptionCode(e.code)]
            assert e.status == "open"
            assert e.severity in (spec.severity.value, Severity.REVIEW.value), (i, e.code)
            assert e.suggested_fix == spec.suggested_fix
    bank = exceptions(env)["inv-057"] if "inv-057" in BY_ID else []
    del bank  # the bank case is asserted by id below
    changed = [i for i, t in BY_ID.items() if "BANK_DETAILS_CHANGED" in t["expected"]["must_raise"]]
    assert len(changed) == 1
    exc = exceptions(env)[changed[0]]
    assert {e.code: e.severity for e in exc}["BANK_DETAILS_CHANGED"] == "block"
    assert invoices(env)[changed[0]].status == "needs_review"


def test_no_explanation_has_a_placeholder_and_every_one_has_a_fix(env: Env) -> None:
    for i, rows in exceptions(env).items():
        for e in rows:
            assert e.explanation.strip() and e.suggested_fix.strip(), (i, e.code)
            assert not re.search(r"[{}]|None|nan", e.explanation), (i, e.code, e.explanation)


def money(t: dict[str, Any], minor: int) -> str:
    return format_money(Money(minor, t["header"]["currency"]))


def test_explanations_carry_the_planted_numbers(env: Env) -> None:
    exc = exceptions(env)
    checked = 0
    for i, t in BY_ID.items():
        by_code = {e.code: e for e in exc[i]}
        for p in t["planted"]:
            code, detail = p["code"], p["detail"]
            if code == "PRICE_VARIANCE":
                n, new = re.match(r"line (\d+): \d+ -> (\d+)", detail).groups()  # type: ignore[union-attr]
                text_ = by_code[code].explanation
                assert f"Line {n} is billed at {money(t, int(new))} per unit" in text_, (i, text_)
            elif code == "QTY_VARIANCE":
                n, po_q, inv_q = re.match(
                    r"line (\d+): PO qty (\d+), invoiced (\d+)", detail
                ).groups()  # type: ignore[union-attr]
                text_ = by_code[code].explanation
                assert f"Line {n} bills {inv_q} units" in text_ and f"is for {po_q}" in text_, (
                    i,
                    text_,
                )
            elif code == "QTY_NOT_RECEIVED":
                n, billed, got = re.match(
                    r"line (\d+): billed (\d+), received (\d+)", detail
                ).groups()  # type: ignore[union-attr]
                text_ = by_code[code].explanation
                assert f"Line {n} bills {billed} units" in text_ and f"only {got} have" in text_, (
                    i,
                    text_,
                )
            elif code == "PO_NOT_FOUND" or code == "RECEIPT_MISSING":
                assert t["header"]["po_number"] in by_code[code].explanation
            elif code == "ABOVE_APPROVAL_LIMIT":
                assert money(t, t["header"]["total_minor"]) in by_code[code].explanation
            else:
                continue
            checked += 1
    assert checked == 23


def test_the_audit_trail_holds_codes_and_ids_only(env: Env) -> None:
    with Session(env.engine) as s:
        mine = list(env.truth_id)
        raised = [
            e
            for e in s.execute(
                select(AuditEvent).where(AuditEvent.event_type == "exception_raised")
            ).scalars()
            if e.invoice_id in mine
        ]
        decided = [
            e
            for e in s.execute(
                select(AuditEvent).where(AuditEvent.event_type == "routing_decided")
            ).scalars()
            if e.invoice_id in mine
        ]
    assert len(decided) == 118
    assert len(raised) == sum(len(v) for v in exceptions(env).values())
    for e in raised:
        assert set(e.data) == {"code", "severity", "unchecked"}
    for e in decided:
        assert set(e.data) == {"status", "route", "reasons", "exceptions"}
    numbers = [t["header"]["invoice_number"] for t in TRUTHS]
    blob = " ".join(str(e.data) for e in raised + decided)
    assert not any(n in blob for n in numbers[:30])


def test_the_status_change_records_why_an_invoice_needs_review(env: Env) -> None:
    with Session(env.engine) as s:
        rows = s.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "status_changed",
                AuditEvent.data["to"].astext == "needs_review",
            )
        ).scalars().all()  # fmt: skip
    mine = [r for r in rows if r.invoice_id in env.truth_id]
    assert mine and all(r.data.get("reason") for r in mine)


def test_running_the_stage_again_changes_nothing(env: Env) -> None:
    invoice_id = env.ids[TRUTHS[10]["file"]]
    with Session(env.engine) as s:
        n_exc = (
            select(func.count())
            .select_from(InvoiceException)
            .where(InvoiceException.invoice_id == invoice_id)
        )
        n_events = (
            select(func.count()).select_from(AuditEvent).where(AuditEvent.invoice_id == invoice_id)
        )
        before = (s.scalar(n_exc), s.scalar(n_events))
        assert route_invoice(s, env.settings, invoice_id, tenant_id=TENANT) is None
        s.commit()
        assert (s.scalar(n_exc), s.scalar(n_events)) == before


def test_another_tenants_job_is_refused(env: Env) -> None:
    with Session(env.engine) as s, pytest.raises(WrongTenant):
        route_invoice(s, env.settings, env.ids[TRUTHS[0]["file"]], tenant_id=uuid.uuid4())


def test_a_missing_check_result_sends_the_invoice_to_review(env: Env) -> None:
    invoice_id = env.ids[TRUTHS[5]["file"]]
    with Session(env.engine) as s:
        assert BY_ID[TRUTHS[5]["id"]]["expected"]["should_clear"]
        s.execute(
            text(
                "delete from check_results where invoice_id = :i and check_code = 'PRICE_VARIANCE'"
            ),
            {"i": str(invoice_id)},
        )
        s.execute(text("delete from exceptions where invoice_id = :i"), {"i": str(invoice_id)})
        s.execute(
            text("update invoices set status = 'checking', route = null where id = :i"),
            {"i": str(invoice_id)},
        )
        assert route_invoice(s, env.settings, invoice_id, tenant_id=TENANT) is None
        inv = s.get_one(Invoice, invoice_id)
        assert inv.status == "needs_review" and inv.route == "review"
        s.rollback()


def test_a_blank_document_failure_is_shown_as_an_unreadable_document_block(env: Env) -> None:
    invoice_id = env.ids[TRUTHS[7]["file"]]
    with Session(env.engine) as s:
        inv = s.get_one(Invoice, invoice_id)
        s.execute(text("delete from exceptions where invoice_id = :i"), {"i": str(invoice_id)})
        inv.status = InvoiceStatus.EXTRACTING.value
        s.flush()
        fail_extraction(s, inv, "UNREADABLE_DOCUMENT")
        s.flush()
        rows = (
            s.execute(select(InvoiceException).where(InvoiceException.invoice_id == invoice_id))
            .scalars()
            .all()
        )
        assert [(r.code, r.severity, r.status) for r in rows] == [
            ("UNREADABLE_DOCUMENT", "block", "open")
        ]
        assert inv.status == "failed" and "blank" in rows[0].explanation
        fail_extraction_other = "SCHEMA_INVALID"
        inv.status = InvoiceStatus.EXTRACTING.value
        s.execute(text("delete from exceptions where invoice_id = :i"), {"i": str(invoice_id)})
        s.flush()
        fail_extraction(s, inv, fail_extraction_other)
        s.flush()
        assert (
            s.scalar(
                select(func.count())
                .select_from(InvoiceException)
                .where(InvoiceException.invoice_id == invoice_id)
            )
            == 0
        )
        s.rollback()


def test_a_currency_with_no_configured_limit_needs_review(env: Env) -> None:
    inv = invoices(env)
    eur_cleared = [
        i
        for i, t in BY_ID.items()
        if t["header"]["currency"] == "EUR" and inv[i].status == "cleared"
    ]
    assert eur_cleared, "the demo tenant has cleared EUR invoices"
    invoice_id = env.ids[BY_ID[eur_cleared[0]]["file"]]
    with Session(env.engine) as s:
        tenant = s.get_one(Tenant, TENANT)
        tenant.settings = {
            k: v for k, v in tenant.settings.items() if k != "approval_amount_limits_minor"
        }
        s.execute(text("delete from exceptions where invoice_id = :i"), {"i": str(invoice_id)})
        s.execute(
            text("update invoices set status = 'checking', route = null where id = :i"),
            {"i": str(invoice_id)},
        )
        s.flush()
        assert route_invoice(s, env.settings, invoice_id, tenant_id=TENANT) is None
        row = s.get_one(Invoice, invoice_id)
        assert row.status == "needs_review" and row.route == "review"
        event = s.execute(
            select(AuditEvent)
            .where(AuditEvent.invoice_id == invoice_id, AuditEvent.event_type == "routing_decided")
            .order_by(AuditEvent.id.desc())
        ).scalars().first()  # fmt: skip
        assert event is not None and "NO_LIMIT" in event.data["reasons"]
        s.rollback()


def test_the_demo_tenant_has_a_limit_for_every_currency_it_uses(env: Env) -> None:
    used = {t["header"]["currency"] for t in TRUTHS if t["header"]["currency"]}
    with Session(env.engine) as s:
        settings = s.get_one(Tenant, TENANT).settings
    limits = {
        "USD": settings["approval_amount_limit_minor"],
        **settings["approval_amount_limits_minor"],
    }
    assert used <= set(limits), used - set(limits)
