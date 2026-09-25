"""M8a over HTTP: sign in, read the queue and an invoice, and act on it, against the real database
after the whole pipeline has run on the seed data (recorded answers)."""

import re
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from intake.checks.duplicates import DEDUPE_JOB
from intake.checks.matching import MATCH_JOB
from intake.checks.pipeline import VALIDATE_JOB
from intake.checks.routing import ROUTE_JOB
from intake.db.models import Tenant
from intake.extract.pipeline import EXTRACT_JOB
from intake.extract.recorded import RecordedClient
from intake.ingest.service import PROCESS_JOB
from intake.ingest.storage import LocalStorage
from intake.main import create_app
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
    TOKEN,
    ingest_all,
    make_settings,
    purge,
)
from tests.support.truth_payload import load_truths

TRUTHS = load_truths(None)
BY_ID = {t["id"]: t for t in TRUTHS}
TODAY = date(2026, 9, 25)
TENANT = uuid.UUID(MASTER["tenant"]["id"])
PASSWORD = "demo-password"


class Env:
    def __init__(self, engine: Engine, client: TestClient, ids: dict[str, uuid.UUID]) -> None:
        self.engine, self.client, self.ids = engine, client, ids
        login = client.post("/auth/login", json={"username": "reviewer", "password": PASSWORD})
        assert login.status_code == 200, login.text
        self.session = {"Authorization": f"Bearer {login.json()['token']}"}
        self.service = {"Authorization": f"Bearer {TOKEN}"}

    def id_of(self, truth_id: str) -> str:
        return str(self.ids[BY_ID[truth_id]["file"]])

    def get(self, path: str, **kw: Any) -> Any:
        return self.client.get(path, headers=self.session, **kw)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.client.post(path, json=body or {}, headers=self.session)


@pytest.fixture(scope="module")
def env(migrated_db_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Env]:
    tmp = tmp_path_factory.mktemp("m8a-api")
    engine = create_engine(migrated_db_url)
    purge(engine)
    settings = make_settings(
        migrated_db_url, tmp, default_tenant_id=MASTER["tenant"]["id"], validation_today=TODAY,
        reviewer_password=PASSWORD, session_secret="test-session-secret",
    )  # fmt: skip
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
    yield Env(engine, TestClient(create_app(settings)), ids)
    with engine.begin() as conn:
        for t in ("review_actions", *PIPELINE_TABLES):
            conn.execute(text(f"delete from {t}"))  # noqa: S608
        for t in ("receipt_lines", "goods_receipts", "po_lines", "purchase_orders", "suppliers"):
            conn.execute(text(f"delete from {t} where tenant_id = :t"), {"t": str(TENANT)})
    engine.dispose()


# ---- signing in -------------------------------------------------------------------------------


def test_the_reviewer_signs_in_and_the_session_names_them(env: Env) -> None:
    me = env.get("/auth/me")
    assert me.status_code == 200 and me.json() == {"user": "reviewer"}


def test_a_wrong_password_is_refused_and_too_many_lock_the_login_for_a_while(env: Env) -> None:
    app = create_app(env.client.app.state.settings)  # type: ignore[attr-defined]
    c = TestClient(app)
    for _ in range(5):
        r = c.post("/auth/login", json={"username": "reviewer", "password": "nope"})
        assert r.status_code == 401
    locked = c.post("/auth/login", json={"username": "reviewer", "password": PASSWORD})
    assert locked.status_code == 429  # even the right password waits


def test_login_is_unavailable_until_a_reviewer_password_is_configured(env: Env) -> None:
    settings = env.client.app.state.settings.model_copy(update={"reviewer_password": ""})  # type: ignore[attr-defined]
    r = TestClient(create_app(settings)).post(
        "/auth/login", json={"username": "reviewer", "password": ""}
    )
    assert r.status_code == 503


def test_everything_but_health_and_login_needs_a_token(env: Env) -> None:
    for path in ("/invoices", "/auth/me", f"/invoices/{env.id_of('inv-001')}"):
        assert env.client.get(path).status_code == 401
    assert env.client.get("/health").status_code == 200


def test_the_service_token_can_read_but_not_act(env: Env) -> None:
    iid = env.id_of("inv-001")
    assert env.client.get("/invoices", headers=env.service).status_code == 200
    r = env.client.post(f"/invoices/{iid}/approve", json={}, headers=env.service)
    assert r.status_code == 403


# ---- the queue --------------------------------------------------------------------------------


def test_the_queue_lists_invoices_needing_a_person_worst_first(env: Env) -> None:
    r = env.get("/invoices", params={"limit": 200})
    assert r.status_code == 200
    body = r.json()
    items = body["items"]
    assert body["total"] == len(items) >= 80
    assert {i["status"] for i in items} <= {"needs_review", "failed"}
    rank = {"block": 3, "review": 2, "info": 1, None: 0}
    keys = [
        (-rank[i["top_exception"]["severity"] if i["top_exception"] else None], i["created_at"])
        for i in items
    ]
    assert keys == sorted(keys)
    first = items[0]
    assert first["top_exception"]["severity"] == "block" and first["open_exceptions"] >= 1
    assert {"id", "supplier_name", "invoice_number", "total_minor", "currency"} <= set(first)


def test_the_queue_can_be_filtered_by_exception_code_and_status_and_paged(env: Env) -> None:
    bank = env.get("/invoices", params={"code": "BANK_DETAILS_CHANGED"}).json()
    assert bank["total"] == 1
    assert bank["items"][0]["top_exception"]["code"] == "BANK_DETAILS_CHANGED"
    cleared = env.get("/invoices", params={"status": "cleared", "limit": 200}).json()
    assert cleared["total"] >= 30 and all(i["open_exceptions"] == 0 for i in cleared["items"])
    page1 = env.get("/invoices", params={"limit": 5}).json()
    page2 = env.get("/invoices", params={"limit": 5, "offset": 5}).json()
    assert len(page1["items"]) == 5 and page1["total"] == page2["total"]
    assert not {i["id"] for i in page1["items"]} & {i["id"] for i in page2["items"]}
    supplier = page1["items"][0]["supplier_id"]
    assert supplier
    only = env.get("/invoices", params={"supplier_id": supplier, "limit": 200}).json()
    assert only["total"] >= 1 and all(i["supplier_id"] == supplier for i in only["items"])


@pytest.mark.parametrize(
    "params",
    [{"status": "nonsense"}, {"limit": 0}, {"limit": 1000}, {"offset": -1}, {"supplier_id": "x"},
     {"code": "NOT_A_CODE"}],
)  # fmt: skip
def test_bad_queue_parameters_are_refused(env: Env, params: dict[str, Any]) -> None:
    assert env.get("/invoices", params=params).status_code == 422


# ---- one invoice ------------------------------------------------------------------------------


def detail(env: Env, truth_id: str) -> dict[str, Any]:
    r = env.get(f"/invoices/{env.id_of(truth_id)}")
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def truth_with(code: str) -> str:
    return next(t["id"] for t in TRUTHS if code in t["expected"]["must_raise"])


def test_an_invoice_shows_fields_with_confidence_and_why_a_field_is_doubtful(env: Env) -> None:
    scan = next(
        t["id"] for t in TRUTHS if t["expected"]["should_clear"] and t["doc_quality"] == "scanned"
    )
    d = detail(env, scan)
    fields = {f["field"]: f for f in d["fields"]}
    number = fields["invoice_number"]
    assert number["weak"] is True and "nothing confirms it" in number["reason"]
    assert 0 < number["confidence"] < 0.8 and number["page"] == 1
    assert fields["total"]["weak"] is False and fields["total"]["reason"] is None
    assert d["document"]["page_count"] >= 1 and d["document"]["doc_quality"] == "scanned"
    assert d["lines"] and {"line_no", "description", "qty", "unit_price_minor"} <= set(
        d["lines"][0]
    )
    assert "LOW_CONFIDENCE" in d["routing_reasons"]


def test_exceptions_carry_their_explanation_and_fix(env: Env) -> None:
    d = detail(env, truth_with("PRICE_VARIANCE"))
    ex = next(e for e in d["exceptions"] if e["code"] == "PRICE_VARIANCE")
    assert ex["status"] == "open" and ex["severity"] == "review"
    assert re.search(r"Line \d+ is billed at", ex["explanation"]) and ex["suggested_fix"]
    assert d["can_approve"] is False and d["approval_blockers"] >= 1


def test_a_duplicate_exception_names_the_other_invoice_for_a_side_by_side_view(env: Env) -> None:
    d = detail(env, truth_with("POSSIBLE_DUPLICATE"))
    ex = next(e for e in d["exceptions"] if e["code"] == "POSSIBLE_DUPLICATE")
    other = ex["related_invoice"]
    assert other and other["id"] != d["id"]
    assert {
        "invoice_number",
        "invoice_date",
        "total_minor",
        "currency",
        "supplier_name",
        "status",
    } <= set(other)
    assert env.get(f"/invoices/{other['id']}").status_code == 200


def test_a_bank_account_is_never_shown_in_full(env: Env) -> None:
    truth = BY_ID[truth_with("BANK_DETAILS_CHANGED")]
    d = detail(env, truth["id"])
    account = truth["header"]["supplier_bank_account"]
    digits = re.sub(r"\W", "", account)
    assert digits[:-4] not in re.sub(r"\W", "", env.get(f"/invoices/{d['id']}").text)
    assert d["bank"]["present"] is True and d["bank"]["masked"].endswith(digits[-4:])
    bank_field = next(f for f in d["fields"] if f["field"] == "supplier_bank_account")
    assert bank_field["value"] == d["bank"]["masked"] and bank_field["raw"] == d["bank"]["masked"]


def test_unknown_and_malformed_invoice_ids(env: Env) -> None:
    assert env.get(f"/invoices/{uuid.uuid4()}").status_code == 404
    assert env.get("/invoices/not-a-uuid").status_code == 422


def test_page_images_are_served_to_a_signed_in_caller_only(env: Env) -> None:
    iid = env.id_of("inv-002")
    r = env.get(f"/invoices/{iid}/pages/1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert (
        r.content[:8] == b"\x89PNG\r\n\x1a\n" and r.headers["x-content-type-options"] == "nosniff"
    )
    assert env.client.get(f"/invoices/{iid}/pages/1").status_code == 401
    assert env.get(f"/invoices/{iid}/pages/0").status_code == 404
    assert env.get(f"/invoices/{iid}/pages/99").status_code == 404
    assert env.get(f"/invoices/{iid}/pages/x").status_code == 422
    assert env.get(f"/invoices/{uuid.uuid4()}/pages/1").status_code == 404


# ---- acting -----------------------------------------------------------------------------------


def test_correcting_a_field_reruns_the_checks_and_returns_the_new_state(env: Env) -> None:
    tid = truth_with("LINE_MATH_MISMATCH")
    iid = env.id_of(tid)
    bad = next(e for e in detail(env, tid)["exceptions"] if e["code"] == "LINE_MATH_MISMATCH")
    assert bad["status"] == "open"
    r = env.post(f"/invoices/{iid}/corrections", {"field": "payment_terms", "value": "Net 60"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] in ("cleared", "needs_review")
    terms = next(f for f in d["fields"] if f["field"] == "payment_terms")
    assert (
        terms["corrected"] is True
        and terms["corrected_by"] == "reviewer"
        and terms["value"] == "Net 60"
    )
    # the untouched problem is found again by the re-run
    assert any(e["code"] == "LINE_MATH_MISMATCH" and e["status"] == "open" for e in d["exceptions"])


def test_refused_actions_say_why_with_a_code(env: Env) -> None:
    iid = env.id_of(truth_with("NO_PO"))
    bad = env.post(f"/invoices/{iid}/corrections", {"field": "total", "value": "abc"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "INVALID_VALUE"
    unknown = env.post(
        f"/invoices/{iid}/corrections", {"field": "supplier_bank_account", "value": "1"}
    )
    assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "UNKNOWN_FIELD"
    blocked = env.post(f"/invoices/{iid}/approve")
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "OPEN_EXCEPTIONS"
    noreason = env.post(f"/invoices/{iid}/reject", {"reason": " "})
    assert noreason.status_code == 422 and noreason.json()["detail"]["code"] == "NOTE_REQUIRED"


def test_a_block_exception_needs_a_note_then_the_invoice_can_be_approved(env: Env) -> None:
    tid = truth_with("BANK_DETAILS_CHANGED")
    d = detail(env, tid)
    for ex in d["exceptions"]:
        without = env.post(f"/exceptions/{ex['id']}/close", {"resolution": "resolved"})
        if ex["severity"] == "block":
            assert (
                without.status_code == 422 and without.json()["detail"]["code"] == "NOTE_REQUIRED"
            )
        r = env.post(
            f"/exceptions/{ex['id']}/close",
            {"resolution": "dismissed", "note": "Verified by phone"},
        )
        assert r.status_code == 200, r.text
    d = env.get(f"/invoices/{d['id']}").json()
    assert d["can_approve"] is True and d["approval_blockers"] == 0
    approved = env.post(f"/invoices/{d['id']}/approve", {"note": "ok"})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    again = env.post(f"/invoices/{d['id']}/approve")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "WRONG_STATUS"


def test_an_invalid_resolution_is_refused(env: Env) -> None:
    iid = env.id_of(truth_with("QTY_VARIANCE"))
    ex = detail(env, truth_with("QTY_VARIANCE"))["exceptions"][0]
    assert env.post(f"/exceptions/{ex['id']}/close", {"resolution": "deleted"}).status_code == 422
    assert (
        env.post(f"/exceptions/{uuid.uuid4()}/close", {"resolution": "resolved"}).status_code == 404
    )
    assert iid


def test_rejecting_and_requesting_information(env: Env) -> None:
    tid = truth_with("QTY_VARIANCE")
    iid = env.id_of(tid)
    asked = env.post(f"/invoices/{iid}/request-info", {"note": "Please confirm the quantity"})
    assert asked.status_code == 200 and asked.json()["info_requested"] is True
    assert asked.json()["status"] == "needs_review"
    rejected = env.post(f"/invoices/{iid}/reject", {"reason": "Quantity not ordered"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    gone = env.get("/invoices").json()["items"]
    assert iid not in {i["id"] for i in gone}


def test_revealing_a_bank_account_needs_a_signed_in_reviewer(env: Env) -> None:
    tid = truth_with("BANK_DETAILS_CHANGED")
    iid = env.id_of(tid)
    assert env.client.post(f"/invoices/{iid}/bank/reveal", headers=env.service).status_code == 403
    r = env.post(f"/invoices/{iid}/bank/reveal")
    assert r.status_code == 200
    wanted = re.sub(r"\W", "", BY_ID[tid]["header"]["supplier_bank_account"]).upper()
    assert re.sub(r"\W", "", r.json()["account"]).upper().endswith(wanted[-4:])
    none = next(
        t["id"] for t in TRUTHS
        if not t["header"].get("supplier_bank_account") and t["expected"]["should_clear"]
    )  # fmt: skip
    assert env.post(f"/invoices/{env.id_of(none)}/bank/reveal").status_code == 404


def test_the_openapi_schema_lists_every_review_route(env: Env) -> None:
    paths = env.client.get("/openapi.json", headers=env.service).json()["paths"]
    for path in (
        "/auth/login", "/auth/me", "/invoices", "/invoices/{invoice_id}",
        "/invoices/{invoice_id}/pages/{page}", "/invoices/{invoice_id}/corrections",
        "/invoices/{invoice_id}/approve", "/invoices/{invoice_id}/reject",
        "/invoices/{invoice_id}/request-info", "/invoices/{invoice_id}/bank/reveal",
        "/exceptions/{exception_id}/close",
    ):  # fmt: skip
        assert path in paths, path
