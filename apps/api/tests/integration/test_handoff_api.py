"""Project B over HTTP: connecting, handing an approved invoice over, reconciling, and what the
responses may and may not contain. The provider is the offline mock, or Xero's real adapter talking
to recorded responses; no network is used."""

import json
import re
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from intake.config import Settings
from intake.core.accounting import Failure
from intake.core.oauth import make_state
from intake.core.statuses import InvoiceStatus
from intake.db.models import AccountingConnection, AccountingHandoff
from intake.main import create_app
from tests.integration.test_handoff_service import ABN, Case

PASSWORD = "demo-password"
SECRET = "test-session-secret-0123456789abcdef"
API_TOKEN = "service-token"
KEY = Fernet.generate_key().decode()


def settings_for(url: str, tenant: str, **kw: Any) -> Settings:
    return Settings(
        database_url=url, api_token=API_TOKEN, reviewer_password=PASSWORD, session_secret=SECRET,
        default_tenant_id=tenant, **kw,
    )  # fmt: skip


class Api:
    def __init__(self, app_client: TestClient) -> None:
        self.c = app_client
        r = app_client.post("/auth/login", json={"username": "reviewer", "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.user = {"Authorization": f"Bearer {r.json()['token']}"}
        self.script = {"Authorization": f"Bearer {API_TOKEN}"}

    def get(self, path: str) -> Any:
        return self.c.get(path, headers=self.user)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.c.post(path, json=body or {}, headers=self.user)


@pytest.fixture
def mock_api(engine: Engine, migrated_db_url: str) -> Iterator[tuple[Case, Api]]:
    case = Case(engine)
    app = create_app(settings_for(migrated_db_url, str(case.tenant)))
    with TestClient(app) as client:
        yield case, Api(client)


def url(case: Case, tail: str = "") -> str:
    return f"/invoices/{case.invoice}/handoff{tail}"


# ---- who may call ---------------------------------------------------------------------------


def test_nothing_is_reachable_without_signing_in(mock_api: tuple[Case, Api]) -> None:
    case, api = mock_api
    for method, path in [
        ("GET", url(case)), ("POST", url(case)), ("POST", url(case, "/reconcile")),
        ("POST", url(case, "/resolve")), ("GET", "/accounting/connection"),
        ("POST", "/accounting/xero/connect"), ("POST", "/accounting/xero/callback"),
    ]:  # fmt: skip
        assert api.c.request(method, path, json={}).status_code == 401, path


def test_a_script_with_only_the_service_token_cannot_trigger_a_handoff(
    mock_api: tuple[Case, Api],
) -> None:
    case, api = mock_api
    for path in (
        url(case),
        url(case, "/reconcile"),
        url(case, "/resolve"),
        "/accounting/xero/connect",
    ):
        assert api.c.post(path, json={}, headers=api.script).status_code == 403, path
    assert api.c.get(url(case), headers=api.script).status_code == 200  # reading status is fine
    assert case.mock.create_calls == 0
    assert case.row_count() == 0


# ---- the offline mock provider --------------------------------------------------------------


def test_the_status_says_what_this_does_and_does_not_do(mock_api: tuple[Case, Api]) -> None:
    case, api = mock_api
    conn = api.get("/accounting/connection").json()
    assert conn["provider"] == "mock" and conn["connected"] and conn["tax_types_configured"]
    assert conn["note"] == (
        "Creates draft bills only. A human must review and approve bills in the accounting "
        "platform."
    )
    body = api.get(url(case)).json()
    assert (body["status"], body["can_request"], body["can_reconcile"]) == (
        "not_started", True, False,
    )  # fmt: skip


def test_an_approved_invoice_is_handed_over_once(mock_api: tuple[Case, Api]) -> None:
    case, api = mock_api
    r = api.post(url(case))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "succeeded" and body["external_bill_id"] == "bill-1"
    assert "approve it in the accounting system" in body["message"]
    assert api.get(url(case)).json()["can_request"] is False
    again = api.post(url(case))
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ALREADY_HANDED_OFF"
    assert case.row_count() == 1


def test_an_unapproved_invoice_is_refused_with_a_reason(
    engine: Engine, migrated_db_url: str
) -> None:
    case = Case(engine, InvoiceStatus.NEEDS_REVIEW)
    with TestClient(create_app(settings_for(migrated_db_url, str(case.tenant)))) as client:
        api = Api(client)
        assert api.get(url(case)).json()["can_request"] is False
        r = api.post(url(case))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_APPROVED"
    assert case.row_count() == 0


def test_an_unknown_invoice_is_not_found(mock_api: tuple[Case, Api]) -> None:
    _, api = mock_api
    missing = "00000000-0000-4000-8000-000000000001"
    assert api.get(f"/invoices/{missing}/handoff").status_code == 404
    assert api.post(f"/invoices/{missing}/handoff").status_code == 404


def test_a_lost_response_shows_as_uncertain_and_can_be_reconciled(
    mock_api: tuple[Case, Api],
) -> None:
    case, api = mock_api
    api.c.app.state.mock_accounting.lose_response(Failure.TIMEOUT)  # type: ignore[attr-defined]
    body = api.post(url(case)).json()
    assert body["status"] == "unknown" and body["manual_action"] is True
    assert "may have been created" in body["message"]
    assert "created." not in body["message"].replace("may have been created.", "")
    status = api.get(url(case)).json()
    assert status["can_reconcile"] is True and status["can_request"] is False
    assert api.post(url(case)).status_code == 409  # no blind second write
    fixed = api.post(url(case, "/reconcile")).json()
    assert fixed["status"] == "succeeded" and fixed["external_bill_id"]
    assert len(api.c.app.state.mock_accounting.bills) == 1  # type: ignore[attr-defined]


def test_a_person_can_say_nothing_was_created_and_try_again(mock_api: tuple[Case, Api]) -> None:
    case, api = mock_api
    api.c.app.state.mock_accounting.fail_before_processing(Failure.TIMEOUT)  # type: ignore[attr-defined]
    assert api.post(url(case)).json()["status"] == "unknown"
    assert api.post(url(case, "/resolve"), {"bill_id": None}).json()["status"] == "failed"
    assert api.post(url(case)).json()["status"] == "succeeded"


def test_resolve_and_reconcile_need_an_unresolved_handoff(mock_api: tuple[Case, Api]) -> None:
    case, api = mock_api
    assert api.post(url(case, "/resolve")).status_code == 404
    assert api.post(url(case, "/reconcile")).status_code == 404


def test_responses_carry_no_secrets_or_provider_bodies(mock_api: tuple[Case, Api]) -> None:
    case, api = mock_api
    texts = [
        api.get("/accounting/connection").text,
        api.post(url(case)).text,
        api.get(url(case)).text,
    ]
    for t in texts:
        low = t.lower()
        assert not re.search(r"token|secret|password|authorization|bearer", low), t


# ---- Xero: connecting and handing over, with recorded responses -----------------------------

CONTACTS = json.dumps(
    {"Contacts": [{"ContactID": "x-c1", "Name": "Acme Pty Ltd", "TaxNumber": ABN,
                   "ContactStatus": "ACTIVE"}]}
)  # fmt: skip
CREATED = json.dumps(
    {"Invoices": [{"InvoiceID": "x-inv-1", "Type": "ACCPAY", "Status": "DRAFT",
                   "Contact": {"ContactID": "x-c1"}, "CurrencyCode": "AUD", "SubTotal": 100.0,
                   "TotalTax": 10.0, "Total": 110.0}]}
)  # fmt: skip


def fake_xero(
    requests: list[httpx.Request], orgs: str = '[{"tenantId":"org-1","tenantName":"Demo"}]'
):  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        path = req.url.path
        if path == "/connect/token":
            return httpx.Response(
                200, json={"access_token": "ACC-1", "refresh_token": "REF-1", "expires_in": 1800}
            )
        if path == "/connections":
            return httpx.Response(200, text=orgs)
        if path.endswith("/Contacts"):
            return httpx.Response(200, text=CONTACTS)
        if path.endswith("/Invoices") and req.method == "GET":
            return httpx.Response(200, text='{"Invoices":[]}')
        if path.endswith("/Invoices") and req.method == "PUT":
            return httpx.Response(200, text=CREATED)
        return httpx.Response(404, text="{}")

    return handler


@pytest.fixture
def xero_api(
    engine: Engine, migrated_db_url: str
) -> Iterator[tuple[Case, Api, list[httpx.Request]]]:
    case = Case(engine)
    seen: list[httpx.Request] = []
    app = create_app(
        settings_for(
            migrated_db_url,
            str(case.tenant),
            accounting_provider="xero",
            accounting_encryption_key=KEY,
            xero_client_id="client-1",
            xero_client_secret="sec-1",
            xero_redirect_uri="https://app.example/accounting/callback",
            accounting_tax_types='{"1000": "INPUT"}',
        )
    )
    app.state.http_client = lambda: httpx.Client(transport=httpx.MockTransport(fake_xero(seen)))
    with TestClient(app) as client:
        yield case, Api(client), seen


def start_connect(api: Api) -> str:
    r = api.post("/accounting/xero/connect")
    assert r.status_code == 200, r.text
    return parse_qs(urlsplit(r.json()["url"]).query)["state"][0]


def test_before_connecting_nothing_can_be_sent(
    xero_api: tuple[Case, Api, list[httpx.Request]],
) -> None:
    case, api, seen = xero_api
    assert api.get("/accounting/connection").json()["connected"] is False
    r = api.post(url(case))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_CONNECTED"
    assert api.get(url(case)).json()["can_request"] is False
    assert seen == []


def test_connect_sends_the_reviewer_to_xero_with_state_pkce_and_the_configured_scopes(
    xero_api: tuple[Case, Api, list[httpx.Request]],
) -> None:
    _, api, _ = xero_api
    link = api.post("/accounting/xero/connect").json()["url"]
    parts = urlsplit(link)
    q = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://login.xero.com/identity/connect/authorize"
    )
    assert q["client_id"] == ["client-1"] and q["code_challenge_method"] == ["S256"]
    assert q["scope"] == ["offline_access accounting.transactions accounting.contacts.read"]
    assert q["state"][0].startswith("o1.") and "client_secret" not in link


def test_a_callback_with_a_bad_or_foreign_state_stores_nothing(
    xero_api: tuple[Case, Api, list[httpx.Request]], engine: Engine
) -> None:
    case, api, seen = xero_api
    forged = make_state("another-secret-another-secret-123456", "reviewer", int(time.time()))
    someone_else = make_state(SECRET, "mallory", int(time.time()))
    expired = make_state(SECRET, "reviewer", int(time.time()) - 5000)
    for state, reason in [
        ("garbage", "BAD_STATE"), (forged, "BAD_STATE"), (someone_else, "WRONG_USER"),
        (expired, "EXPIRED"), (None, "MISSING"),
    ]:  # fmt: skip
        r = api.post("/accounting/xero/callback", {"code": "abc", "state": state})
        assert r.status_code == 400 and r.json()["detail"]["code"] == reason, (state, r.text)
    denied = api.post("/accounting/xero/callback", {"error": "access_denied", "state": "x"})
    assert denied.json()["detail"]["code"] == "DENIED"
    assert seen == []  # nothing was exchanged with Xero
    with Session(engine) as s:
        assert (
            s.scalars(
                select(AccountingConnection).where(AccountingConnection.tenant_id == case.tenant)
            ).first()
            is None
        )


def connect_xero(api: Api) -> Any:
    state = start_connect(api)
    return api.post("/accounting/xero/callback", {"code": "abc", "state": state})


def test_a_good_callback_stores_encrypted_tokens_and_returns_none_of_them(
    xero_api: tuple[Case, Api, list[httpx.Request]], engine: Engine
) -> None:
    case, api, seen = xero_api
    r = connect_xero(api)
    assert r.status_code == 200, r.text
    assert r.json()["connected"] is True and r.json()["organisation_name"] == "Demo"
    assert not re.search(r"ACC-1|REF-1|token", r.text, re.I)
    exchange = next(q for q in seen if q.url.path == "/connect/token")
    assert b"code_verifier=" in exchange.content and b"code=abc" in exchange.content
    with engine.connect() as c:
        stored = c.execute(
            text("select tokens_encrypted from accounting_connections where tenant_id=:t"),
            {"t": case.tenant},
        ).scalar_one()
    assert "ACC-1" not in stored and "REF-1" not in stored
    assert api.get("/accounting/connection").json()["connected"] is True
    events = case.tenant_events()
    assert "accounting_connected" in events


def test_a_connect_that_authorised_several_organisations_stores_nothing(
    engine: Engine, migrated_db_url: str
) -> None:
    case = Case(engine)
    seen: list[httpx.Request] = []
    two = '[{"tenantId":"a","tenantName":"A"},{"tenantId":"b","tenantName":"B"}]'
    app = create_app(
        settings_for(
            migrated_db_url, str(case.tenant), accounting_provider="xero",
            accounting_encryption_key=KEY, xero_client_id="c", xero_redirect_uri="https://x/cb",
        )
    )  # fmt: skip
    app.state.http_client = lambda: httpx.Client(
        transport=httpx.MockTransport(fake_xero(seen, two))
    )
    with TestClient(app) as client:
        api = Api(client)
        r = connect_xero(api)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CHOOSE_ORGANISATION"
    with Session(engine) as s:
        assert (
            s.scalars(
                select(AccountingConnection).where(AccountingConnection.tenant_id == case.tenant)
            ).first()
            is None
        )


def test_xero_settings_must_be_complete_before_connecting(
    engine: Engine, migrated_db_url: str
) -> None:
    case = Case(engine)
    app = create_app(settings_for(migrated_db_url, str(case.tenant), accounting_provider="xero"))
    with TestClient(app) as client:
        r = Api(client).post("/accounting/xero/connect")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "XERO_NOT_CONFIGURED"


def test_the_offline_mock_provider_cannot_be_connected_to_xero(mock_api: tuple[Case, Api]) -> None:
    _, api = mock_api
    r = api.post("/accounting/xero/connect")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_XERO"


def test_end_to_end_a_connected_xero_organisation_gets_one_draft_bill(
    xero_api: tuple[Case, Api, list[httpx.Request]], engine: Engine
) -> None:
    case, api, seen = xero_api
    assert connect_xero(api).status_code == 200
    seen.clear()
    r = api.post(url(case))
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["external_bill_id"], body["provider"]) == (
        "succeeded", "x-inv-1", "xero",
    )  # fmt: skip
    writes = [q for q in seen if q.method != "GET"]
    assert [(q.method, q.url.path) for q in writes] == [("PUT", "/api.xro/2.0/Invoices")]
    sent = json.loads(writes[0].content)["Invoices"][0]
    assert (sent["Type"], sent["Status"]) == ("ACCPAY", "DRAFT")
    assert writes[0].headers["xero-tenant-id"] == "org-1"
    assert writes[0].headers["Authorization"] == "Bearer ACC-1"
    assert writes[0].headers["Idempotency-Key"].startswith("ii1-")
    assert api.post(url(case)).status_code == 409  # not twice
    assert len([q for q in seen if q.method == "PUT"]) == 1
    with Session(engine) as s:
        h = s.scalars(
            select(AccountingHandoff).where(AccountingHandoff.tenant_id == case.tenant)
        ).one()
        assert (h.provider, h.organisation_id, h.status) == ("xero", "org-1", "succeeded")


def test_no_request_to_the_provider_ever_approves_or_pays(
    xero_api: tuple[Case, Api, list[httpx.Request]],
) -> None:
    case, api, seen = xero_api
    connect_xero(api)
    api.post(url(case))
    for q in seen:
        low = f"{q.method} {q.url}".lower()
        assert "payment" not in low and "approve" not in low and "authorised" not in low
        if q.method == "PUT" and q.url.path.endswith("/Invoices"):
            assert json.loads(q.content)["Invoices"][0]["Status"] == "DRAFT"
