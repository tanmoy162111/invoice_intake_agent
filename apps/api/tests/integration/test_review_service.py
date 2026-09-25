"""M8a: what a reviewer can do, end to end over the seed data (real worker and database, recorded
answers): correct a field and see the checks re-run, close exceptions, approve, reject, ask for
information, and reveal a bank account. Every action is a `review_actions` row plus audit events."""

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from intake.checks.duplicates import DEDUPE_JOB
from intake.checks.matching import MATCH_JOB
from intake.checks.pipeline import VALIDATE_JOB
from intake.checks.routing import ROUTE_JOB
from intake.core.review import ReviewProblem
from intake.db.models import (
    AuditEvent,
    CheckResult,
    FieldExtraction,
    Invoice,
    InvoiceException,
    ReviewActionRow,
    Tenant,
)
from intake.extract.pipeline import EXTRACT_JOB
from intake.extract.recorded import RecordedClient
from intake.ingest.service import PROCESS_JOB
from intake.ingest.storage import LocalStorage
from intake.review.service import (
    NotFound,
    ReviewRefused,
    approve_invoice,
    close_exception,
    correct_field,
    reject_invoice,
    request_info,
    reveal_bank_account,
)
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
BY_ID = {t["id"]: t for t in TRUTHS}
TODAY = date(2026, 9, 25)
TENANT = uuid.UUID(MASTER["tenant"]["id"])
USER = "reviewer"


class Env:
    def __init__(self, engine: Engine, settings: Any, ids: dict[str, uuid.UUID]) -> None:
        self.engine, self.settings, self.ids = engine, settings, ids
        self.vault = BankVault(BANK_KEY)

    def invoice_id(self, truth_id: str) -> uuid.UUID:
        return self.ids[BY_ID[truth_id]["file"]]


@pytest.fixture(scope="module")
def env(migrated_db_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Env]:
    tmp = tmp_path_factory.mktemp("m8a")
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
        for t in ("review_actions", *PIPELINE_TABLES):
            conn.execute(text(f"delete from {t}"))  # noqa: S608
        for t in ("receipt_lines", "goods_receipts", "po_lines", "purchase_orders", "suppliers"):
            conn.execute(text(f"delete from {t} where tenant_id = :t"), {"t": str(TENANT)})
    engine.dispose()


def open_codes(s: Session, invoice_id: uuid.UUID) -> set[str]:
    rows = s.execute(
        select(InvoiceException).where(
            InvoiceException.invoice_id == invoice_id, InvoiceException.status == "open"
        )
    ).scalars()
    return {r.code for r in rows}


def events(s: Session, invoice_id: uuid.UUID, kind: str) -> list[AuditEvent]:
    return list(
        s.execute(
            select(AuditEvent).where(
                AuditEvent.invoice_id == invoice_id, AuditEvent.event_type == kind
            )
        ).scalars()
    )


def actions(s: Session, invoice_id: uuid.UUID, kind: str) -> list[ReviewActionRow]:
    return list(
        s.execute(
            select(ReviewActionRow).where(
                ReviewActionRow.invoice_id == invoice_id, ReviewActionRow.action == kind
            )
        ).scalars()
    )


def truth_with(code: str) -> str:
    return next(t["id"] for t in TRUTHS if code in t["expected"]["must_raise"])


def cleared_invoice(env: Env) -> uuid.UUID:
    with Session(env.engine) as s:
        return s.scalars(
            select(Invoice.id).where(
                Invoice.id.in_(list(env.ids.values())), Invoice.status == "cleared"
            )
        ).first()  # type: ignore[return-value]


# ---- correcting a field re-runs the checks ---------------------------------------------------


def fix_total(env: Env, s: Session, invoice_id: uuid.UUID) -> str:
    """The total that makes subtotal + tax add up, as a reviewer would type it."""
    row = s.execute(
        select(CheckResult).where(
            CheckResult.invoice_id == invoice_id, CheckResult.check_code == "TOTAL_MISMATCH"
        )
    ).scalar_one()
    bad = next(c for c in row.details["checks"] if c["name"] == "SUBTOTAL_PLUS_TAX_IS_TOTAL")
    return f"{Decimal(bad['expected_minor']) / 100:.2f}"


def total_mismatch_invoice(env: Env) -> uuid.UUID:
    """A needs-review invoice whose total does not equal subtotal plus tax."""
    for t in TRUTHS:
        if "TOTAL_MISMATCH" in t["expected"]["must_raise"]:
            iid = env.ids[t["file"]]
            with Session(env.engine) as s:
                rows = s.execute(
                    select(CheckResult).where(
                        CheckResult.invoice_id == iid, CheckResult.check_code == "TOTAL_MISMATCH"
                    )
                ).scalars()
                if any(
                    c["name"] == "SUBTOTAL_PLUS_TAX_IS_TOTAL" and not c["ok"]
                    for r in rows
                    for c in r.details["checks"]
                ):
                    return iid
    raise AssertionError("no invoice with a total that does not add up")


def test_correcting_the_total_reruns_the_checks_and_removes_the_exception(env: Env) -> None:
    iid = total_mismatch_invoice(env)
    with Session(env.engine) as s:
        assert "TOTAL_MISMATCH" in open_codes(s, iid)
        typed = fix_total(env, s, iid)
        correct_field(
            s,
            env.settings,
            tenant_id=TENANT,
            invoice_id=iid,
            actor=USER,
            field="total",
            value=typed,
        )
        s.commit()
    with Session(env.engine) as s:
        inv = s.get_one(Invoice, iid)
        assert inv.status in ("cleared", "needs_review") and inv.route is not None
        assert "TOTAL_MISMATCH" not in open_codes(s, iid)
        assert inv.total_minor == int(Decimal(typed) * 100)
        field = s.execute(
            select(FieldExtraction).where(
                FieldExtraction.invoice_id == iid, FieldExtraction.field == "total"
            )
        ).scalar_one()
        assert (field.corrected_value, field.corrected_by) == (typed, USER)
        assert field.normalized_value == str(inv.total_minor)
        row = s.execute(
            select(CheckResult).where(
                CheckResult.invoice_id == iid, CheckResult.check_code == "TOTAL_MISMATCH"
            )
        ).scalar_one()
        assert row.passed  # a single fresh result, not the old one plus a new one
        (act,) = actions(s, iid, "correct_field")
        assert act.user_id == USER and act.payload["field"] == "total"
        assert len(events(s, iid, "field_corrected")) == 1
        assert set(events(s, iid, "field_corrected")[0].data) == {"field"}
        assert len(events(s, iid, "routing_decided")) == 2  # the first run and the re-run


def test_exceptions_that_no_longer_apply_are_closed_by_the_system(env: Env) -> None:
    iid = total_mismatch_invoice_after_fix(env)
    with Session(env.engine) as s:
        old = s.execute(
            select(InvoiceException).where(
                InvoiceException.invoice_id == iid, InvoiceException.code == "TOTAL_MISMATCH"
            )
        ).scalar_one()
        assert (old.status, old.resolved_by) == ("resolved", "system")
        assert "Re-checked" in (old.resolution_note or "")
        assert any(e.data["code"] == "TOTAL_MISMATCH" for e in events(s, iid, "exception_closed"))


def total_mismatch_invoice_after_fix(env: Env) -> uuid.UUID:
    with Session(env.engine) as s:
        rows = s.execute(
            select(InvoiceException).where(
                InvoiceException.code == "TOTAL_MISMATCH",
                InvoiceException.resolved_by == "system",
            )
        ).scalars()
        return next(r.invoice_id for r in rows if r.invoice_id in env.ids.values())


def test_a_correction_that_cannot_be_read_changes_nothing(env: Env) -> None:
    iid = env.invoice_id(truth_with("PRICE_VARIANCE"))
    with Session(env.engine) as s:
        before = (s.get_one(Invoice, iid).total_minor, len(events(s, iid, "field_corrected")))
        with pytest.raises(ReviewRefused) as e:
            correct_field(
                s, env.settings, tenant_id=TENANT, invoice_id=iid, actor=USER,
                field="total", value="not a number",
            )  # fmt: skip
        assert e.value.problem is ReviewProblem.INVALID_VALUE
        s.rollback()
    with Session(env.engine) as s:
        assert (
            s.get_one(Invoice, iid).total_minor,
            len(events(s, iid, "field_corrected")),
        ) == before


@pytest.mark.parametrize(
    ("field", "value", "problem"),
    [
        ("supplier_bank_account", "DE00", ReviewProblem.UNKNOWN_FIELD),
        ("line.1.quantity", "3", ReviewProblem.UNKNOWN_FIELD),
        ("invoice_number", "", ReviewProblem.VALUE_REQUIRED),
        ("invoice_date", "03/04/2026", ReviewProblem.INVALID_VALUE),
    ],
)
def test_corrections_the_rules_refuse(
    env: Env, field: str, value: str, problem: ReviewProblem
) -> None:
    iid = env.invoice_id(truth_with("PRICE_VARIANCE"))
    with Session(env.engine) as s, pytest.raises(ReviewRefused) as e:
        correct_field(
            s, env.settings, tenant_id=TENANT, invoice_id=iid, actor=USER, field=field, value=value
        )
    assert e.value.problem is problem


def test_a_reviewers_earlier_decision_survives_a_re_check(env: Env) -> None:
    iid = env.invoice_id(truth_with("PRICE_VARIANCE"))
    with Session(env.engine) as s:
        target = s.execute(
            select(InvoiceException).where(
                InvoiceException.invoice_id == iid, InvoiceException.code == "PRICE_VARIANCE"
            )
        ).scalar_one()
        close_exception(
            s,
            tenant_id=TENANT,
            exception_id=target.id,
            actor=USER,
            resolution="dismissed",
            note="Agreed price rise",
        )
        correct_field(
            s, env.settings, tenant_id=TENANT, invoice_id=iid, actor=USER,
            field="payment_terms", value="Net 45",
        )  # fmt: skip
        s.commit()
    with Session(env.engine) as s:
        assert "PRICE_VARIANCE" not in open_codes(s, iid)
        kept = s.execute(
            select(InvoiceException).where(
                InvoiceException.invoice_id == iid, InvoiceException.code == "PRICE_VARIANCE"
            )
        ).scalars().all()  # fmt: skip
        assert [(k.status, k.resolved_by, k.resolution_note) for k in kept] == [
            ("dismissed", USER, "Agreed price rise")
        ]


# ---- closing exceptions -----------------------------------------------------------------------


def test_a_block_exception_needs_a_note_and_the_decision_is_recorded(env: Env) -> None:
    iid = env.invoice_id(truth_with("BANK_DETAILS_CHANGED"))
    with Session(env.engine) as s:
        blocked = s.execute(
            select(InvoiceException).where(
                InvoiceException.invoice_id == iid, InvoiceException.code == "BANK_DETAILS_CHANGED"
            )
        ).scalar_one()
        assert blocked.severity == "block"
        blocked_id = blocked.id
        with pytest.raises(ReviewRefused) as e:
            close_exception(
                s, tenant_id=TENANT, exception_id=blocked_id, actor=USER, resolution="resolved",
                note="  ",
            )  # fmt: skip
        assert e.value.problem is ReviewProblem.NOTE_REQUIRED
        close_exception(
            s, tenant_id=TENANT, exception_id=blocked_id, actor=USER, resolution="resolved",
            note="Phoned the number on file: confirmed",
        )  # fmt: skip
        s.commit()
    with Session(env.engine) as s:
        done = s.get_one(InvoiceException, blocked_id)
        assert (done.status, done.resolved_by) == ("resolved", USER)
        assert done.resolution_note == "Phoned the number on file: confirmed"
        (act,) = actions(s, iid, "dismiss_exception")
        assert (
            act.payload["resolution"] == "resolved"
            and act.payload["code"] == "BANK_DETAILS_CHANGED"
        )
        (ev,) = [e for e in events(s, iid, "exception_closed") if e.actor_id == USER]
        assert set(ev.data) == {"code", "resolution"}
        with pytest.raises(ReviewRefused) as again:  # already closed
            close_exception(
                s, tenant_id=TENANT, exception_id=blocked_id, actor=USER, resolution="dismissed",
                note="x",
            )  # fmt: skip
        assert again.value.problem is ReviewProblem.WRONG_STATUS


# ---- approving and rejecting ------------------------------------------------------------------


def test_an_invoice_with_an_open_exception_cannot_be_approved(env: Env) -> None:
    iid = env.invoice_id(truth_with("RECEIPT_MISSING"))
    with Session(env.engine) as s, pytest.raises(ReviewRefused) as e:
        approve_invoice(s, tenant_id=TENANT, invoice_id=iid, actor=USER)
    assert e.value.problem is ReviewProblem.OPEN_EXCEPTIONS


def test_closing_every_exception_allows_approval_and_it_is_final(env: Env) -> None:
    iid = env.invoice_id(truth_with("RECEIPT_MISSING"))
    with Session(env.engine) as s:
        for ex in s.execute(
            select(InvoiceException).where(
                InvoiceException.invoice_id == iid, InvoiceException.status == "open"
            )
        ).scalars():
            close_exception(
                s, tenant_id=TENANT, exception_id=ex.id, actor=USER, resolution="dismissed",
                note="Checked with purchasing",
            )  # fmt: skip
        approve_invoice(s, tenant_id=TENANT, invoice_id=iid, actor=USER, note="ok")
        s.commit()
    with Session(env.engine) as s:
        assert s.get_one(Invoice, iid).status == "approved"
        assert len(actions(s, iid, "approve")) == 1
        changes = [e.data for e in events(s, iid, "status_changed")]
        assert {"from": "needs_review", "to": "approved"} in [
            {k: v for k, v in c.items() if k in ("from", "to")} for c in changes
        ]
        assert any(
            e.actor_id == USER and e.actor_type == "user" for e in events(s, iid, "status_changed")
        )
        for attempt in (
            lambda: approve_invoice(s, tenant_id=TENANT, invoice_id=iid, actor=USER),
            lambda: reject_invoice(s, tenant_id=TENANT, invoice_id=iid, actor=USER, reason="late"),
            lambda: correct_field(
                s,
                env.settings,
                tenant_id=TENANT,
                invoice_id=iid,
                actor=USER,
                field="payment_terms",
                value="Net 10",
            ),
        ):
            with pytest.raises(ReviewRefused) as e:
                attempt()
            assert e.value.problem is ReviewProblem.WRONG_STATUS


def test_a_cleared_invoice_can_be_approved_directly(env: Env) -> None:
    iid = cleared_invoice(env)
    with Session(env.engine) as s:
        approve_invoice(s, tenant_id=TENANT, invoice_id=iid, actor=USER)
        s.commit()
        assert s.get_one(Invoice, iid).status == "approved"


def test_rejecting_needs_a_reason_and_works_on_an_invoice_that_needs_review(env: Env) -> None:
    iid = env.invoice_id(truth_with("QTY_VARIANCE"))
    with Session(env.engine) as s:
        with pytest.raises(ReviewRefused) as e:
            reject_invoice(s, tenant_id=TENANT, invoice_id=iid, actor=USER, reason=" ")
        assert e.value.problem is ReviewProblem.NOTE_REQUIRED
        reject_invoice(s, tenant_id=TENANT, invoice_id=iid, actor=USER, reason="Not ordered")
        s.commit()
    with Session(env.engine) as s:
        assert s.get_one(Invoice, iid).status == "rejected"
        (act,) = actions(s, iid, "reject")
        assert act.payload["reason"] == "Not ordered"


def test_a_cleared_invoice_can_be_rejected(env: Env) -> None:
    with Session(env.engine) as s:
        iid = s.scalars(
            select(Invoice.id).where(
                Invoice.id.in_(list(env.ids.values())), Invoice.status == "cleared"
            )
        ).first()
        assert iid is not None
        reject_invoice(s, tenant_id=TENANT, invoice_id=iid, actor=USER, reason="Wrong supplier")
        s.commit()
        assert s.get_one(Invoice, iid).status == "rejected"


# ---- asking for information, revealing a bank account -----------------------------------------


def test_requesting_information_is_logged_and_changes_no_status(env: Env) -> None:
    iid = env.invoice_id(truth_with("NO_PO"))
    with Session(env.engine) as s:
        before = s.get_one(Invoice, iid).status
        request_info(s, tenant_id=TENANT, invoice_id=iid, actor=USER, note="Please send the PO")
        s.commit()
    with Session(env.engine) as s:
        assert s.get_one(Invoice, iid).status == before
        (act,) = actions(s, iid, "request_info")
        assert act.payload["note"] == "Please send the PO"
        assert len(events(s, iid, "info_requested")) == 1


def test_revealing_a_bank_account_returns_it_and_is_logged_without_it(env: Env) -> None:
    iid = env.invoice_id(truth_with("BANK_DETAILS_CHANGED"))
    truth = BY_ID[truth_with("BANK_DETAILS_CHANGED")]
    with Session(env.engine) as s:
        account = reveal_bank_account(s, env.vault, tenant_id=TENANT, invoice_id=iid, actor=USER)
        s.commit()
    assert (
        account.replace(" ", "")
        .upper()
        .endswith(truth["header"]["supplier_bank_account"].replace(" ", "").upper()[-4:])
    )
    with Session(env.engine) as s:
        (ev,) = events(s, iid, "bank_details_revealed")
        assert (ev.actor_type, ev.actor_id, ev.data) == ("user", USER, {})
        blob = " ".join(str(e.data) for e in s.execute(select(AuditEvent)).scalars())
        assert account not in blob


def test_an_invoice_without_a_bank_account_has_nothing_to_reveal(env: Env) -> None:
    with Session(env.engine) as s:
        missing = s.scalars(
            select(Invoice.id).where(
                Invoice.id.in_(list(env.ids.values())),
                ~Invoice.id.in_(
                    select(FieldExtraction.invoice_id).where(
                        FieldExtraction.field == "supplier_bank_account",
                        FieldExtraction.raw_value.is_not(None),
                    )
                ),
            )
        ).first()
        assert missing is not None
        with pytest.raises(NotFound):
            reveal_bank_account(s, env.vault, tenant_id=TENANT, invoice_id=missing, actor=USER)


# ---- isolation --------------------------------------------------------------------------------


def test_another_tenant_cannot_touch_an_invoice_or_an_exception(env: Env) -> None:
    other = uuid.uuid4()
    iid = env.invoice_id(truth_with("NO_PO"))
    with Session(env.engine) as s:
        ex = s.scalars(select(InvoiceException).where(InvoiceException.invoice_id == iid)).first()
        assert ex is not None
        for attempt in (
            lambda: approve_invoice(s, tenant_id=other, invoice_id=iid, actor=USER),
            lambda: reject_invoice(s, tenant_id=other, invoice_id=iid, actor=USER, reason="x"),
            lambda: request_info(s, tenant_id=other, invoice_id=iid, actor=USER),
            lambda: correct_field(
                s,
                env.settings,
                tenant_id=other,
                invoice_id=iid,
                actor=USER,
                field="total",
                value="1",
            ),
            lambda: close_exception(
                s, tenant_id=other, exception_id=ex.id, actor=USER, resolution="dismissed", note="x"
            ),
            lambda: reveal_bank_account(s, env.vault, tenant_id=other, invoice_id=iid, actor=USER),
        ):
            with pytest.raises(NotFound):
                attempt()
        assert attempt is not None
