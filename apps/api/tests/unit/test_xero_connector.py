"""Contract tests for the Xero adapter against recorded, Xero-shaped responses (httpx MockTransport,
no network). These show the adapter sends what Xero's published API description says and reads what
it says back. They do not show that a real Xero organisation accepts it: see docs/connectors.md."""

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from intake.connectors.accounting import ProviderError
from intake.connectors.tokens import Tokens
from intake.connectors.xero import (
    XeroConnector,
    dumps,
    exchange_code,
    list_organisations,
    make_refresher,
    to_decimal,
    to_minor,
)
from intake.core.accounting import BillDraft, BillLine, Failure

ORG = "org-123"


class FakeTokens:
    def __init__(self) -> None:
        self.token, self.refreshed = "access-1", 0

    def access_token(self) -> str:
        return self.token

    def refresh(self, rejected: str) -> str:
        self.refreshed += 1
        self.token = f"access-{self.refreshed + 1}"
        return self.token


def bill(**kw: object) -> BillDraft:
    base: dict[str, object] = dict(
        contact_id="c-1", invoice_number="INV-001", invoice_date=date(2026, 3, 1),
        due_date=date(2026, 3, 31), currency="AUD", subtotal_minor=10000, tax_minor=1000,
        total_minor=11000,
        lines=(
            BillLine("Printer paper", 10000, "INPUT", Decimal("2"), 5000),
            BillLine("Delivery", 0, "INPUT"),
        ),
    )  # fmt: skip
    base.update(kw)
    return BillDraft(**base)  # type: ignore[arg-type]


INVOICE_OK = (
    '{"Invoices":[{"InvoiceID":"inv-9","Type":"ACCPAY","Status":"DRAFT",'
    '"Contact":{"ContactID":"c-1"},"CurrencyCode":"AUD","SubTotal":100.00,"TotalTax":10.00,'
    '"Total":110.00}]}'
)


class Server:
    """Records every request and answers with whatever the test queued."""

    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def connector(*answers: httpx.Response | Exception) -> tuple[XeroConnector, Server, FakeTokens]:
    server, tokens = Server(*answers), FakeTokens()
    return (
        XeroConnector(ORG, tokens, httpx.Client(transport=httpx.MockTransport(server))),
        server,
        tokens,
    )


def ok(text: str = INVOICE_OK, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(status, text=text, headers=headers)


# ---- creating a draft bill ------------------------------------------------------------------


def test_the_create_request_is_a_draft_supplier_bill_with_exact_amounts() -> None:
    c, server, _ = connector(ok())
    c.create_draft_bill(bill(), "key-1")
    [req] = server.requests
    assert (req.method, req.url.path, dict(req.url.params)) == (
        "PUT", "/api.xro/2.0/Invoices", {"summarizeErrors": "true"},
    )  # fmt: skip
    assert req.headers["xero-tenant-id"] == ORG
    assert req.headers["Authorization"] == "Bearer access-1"
    assert req.headers["Idempotency-Key"] == "key-1"
    assert req.headers["Content-Type"] == "application/json"
    raw = req.content.decode()
    body = json.loads(raw)
    [inv] = body["Invoices"]
    assert (inv["Type"], inv["Status"], inv["LineAmountTypes"]) == ("ACCPAY", "DRAFT", "Exclusive")
    assert inv["Contact"] == {"ContactID": "c-1"}
    assert (inv["InvoiceNumber"], inv["Date"], inv["DueDate"], inv["CurrencyCode"]) == (
        "INV-001", "2026-03-01", "2026-03-31", "AUD",
    )  # fmt: skip
    first, second = inv["LineItems"]
    assert first == {
        "Description": "Printer paper", "LineAmount": 100.0, "TaxType": "INPUT",
        "Quantity": 2, "UnitAmount": 50.0,
    }  # fmt: skip
    assert "AccountCode" not in first and "Quantity" not in second  # nothing invented
    assert '"LineAmount":100.00' in raw and '"UnitAmount":50.00' in raw  # written as exact decimals


def test_a_bill_without_a_due_date_sends_none() -> None:
    c, server, _ = connector(ok())
    c.create_draft_bill(bill(due_date=None), "k")
    assert "DueDate" not in json.loads(server.requests[0].content)["Invoices"][0]


def test_the_answer_becomes_a_returned_bill_in_minor_units() -> None:
    c, _, _ = connector(ok())
    got = c.create_draft_bill(bill(), "k")
    assert (got.bill_id, got.status, got.contact_id, got.currency) == (
        "inv-9",
        "DRAFT",
        "c-1",
        "AUD",
    )
    assert (got.subtotal_minor, got.tax_minor, got.total_minor) == (10000, 1000, 11000)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"Invoices":[]}',
        '{"Invoices":[{},{}]}',
        '{"Invoices":[{"InvoiceID":"x"}]}',
        INVOICE_OK.replace("100.00", "100.001"),  # more decimals than the currency has
    ],
)
def test_an_answer_that_cannot_be_read_is_a_bad_response(text: str) -> None:
    c, _, _ = connector(ok(text))
    with pytest.raises(ProviderError) as err:
        c.create_draft_bill(bill(), "k")
    assert err.value.failure is Failure.BAD_RESPONSE


# ---- how failures are told apart ------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "failure"),
    [
        (ok("{}", 403), Failure.FORBIDDEN),
        (ok("{}", 400), Failure.REJECTED),
        (ok("{}", 404), Failure.REJECTED),
        (ok("{}", 409), Failure.CONFLICT),
        (ok("{}", 500), Failure.SERVER_ERROR),
        (ok("{}", 503), Failure.SERVER_ERROR),
        (httpx.ConnectError("x"), Failure.CONNECT_FAILED),
        (httpx.ConnectTimeout("x"), Failure.CONNECT_FAILED),
        (httpx.ReadTimeout("x"), Failure.TIMEOUT),
        (httpx.WriteTimeout("x"), Failure.TIMEOUT),
        (httpx.RemoteProtocolError("x"), Failure.CONNECTION_LOST),
        (httpx.ReadError("x"), Failure.CONNECTION_LOST),
    ],
)
def test_provider_failures_are_classified(
    answer: httpx.Response | Exception, failure: Failure
) -> None:
    c, _, _ = connector(answer)
    with pytest.raises(ProviderError) as err:
        c.create_draft_bill(bill(), "k")
    assert err.value.failure is failure


def test_rate_limiting_carries_retry_after() -> None:
    c, _, _ = connector(ok("{}", 429, **{"Retry-After": "12"}))
    with pytest.raises(ProviderError) as err:
        c.create_draft_bill(bill(), "k")
    assert (err.value.failure, err.value.retry_after_s) == (Failure.RATE_LIMITED, 12)


def test_rate_limiting_without_a_usable_header_has_no_wait() -> None:
    c, _, _ = connector(ok("{}", 429, **{"Retry-After": "soon"}))
    with pytest.raises(ProviderError) as err:
        c.create_draft_bill(bill(), "k")
    assert err.value.retry_after_s is None


def test_an_expired_token_is_refreshed_once_and_the_request_sent_again() -> None:
    c, server, tokens = connector(ok("{}", 401), ok())
    c.create_draft_bill(bill(), "k")
    assert tokens.refreshed == 1
    assert [r.headers["Authorization"] for r in server.requests] == [
        "Bearer access-1",
        "Bearer access-2",
    ]


def test_a_token_that_is_still_refused_is_an_authorisation_failure_not_a_loop() -> None:
    c, server, tokens = connector(ok("{}", 401))
    with pytest.raises(ProviderError) as err:
        c.create_draft_bill(bill(), "k")
    assert err.value.failure is Failure.UNAUTHORISED
    assert tokens.refreshed == 1 and len(server.requests) == 2


def test_no_error_message_contains_a_token() -> None:
    c, _, _ = connector(ok("{}", 401))
    with pytest.raises(ProviderError) as err:
        c.create_draft_bill(bill(), "k")
    assert "access-" not in str(err.value) and "access-" not in repr(err.value)


# ---- reading --------------------------------------------------------------------------------


def test_contacts_are_searched_by_name_and_tax_number_without_duplicates() -> None:
    page = (
        '{"Contacts":[{"ContactID":"c-1","Name":"Acme","TaxNumber":"51824753556",'
        '"ContactStatus":"ACTIVE"},'
        '{"ContactID":"c-2","Name":"Acme Old","ContactStatus":"ARCHIVED"}]}'
    )
    c, server, _ = connector(ok(page))
    got = c.find_contacts("Acme Pty Ltd", "51824753556")
    assert [r.url.params["searchTerm"] for r in server.requests] == ["Acme Pty Ltd", "51824753556"]
    assert [(x.contact_id, x.tax_number, x.active) for x in got] == [
        ("c-1", "51824753556", True), ("c-2", None, False),
    ]  # fmt: skip
    assert all(r.method == "GET" for r in server.requests)


def test_a_contact_without_an_id_is_a_bad_response() -> None:
    c, _, _ = connector(ok('{"Contacts":[{"Name":"x"}]}'))
    with pytest.raises(ProviderError) as err:
        c.find_contacts("x", None)
    assert err.value.failure is Failure.BAD_RESPONSE


def test_a_contact_that_no_longer_exists_is_none() -> None:
    c, server, _ = connector(ok("{}", 404))
    assert c.get_contact("gone") is None
    assert server.requests[0].url.path.endswith("/Contacts/gone")


def test_other_failures_reading_a_contact_are_raised() -> None:
    c, _, _ = connector(ok("{}", 500))
    with pytest.raises(ProviderError):
        c.get_contact("c-1")


def test_a_contact_is_returned_by_id() -> None:
    c, _, _ = connector(ok('{"Contacts":[{"ContactID":"c-1","Name":"Acme"}]}'))
    got = c.get_contact("c-1")
    assert got is not None and got.name == "Acme" and got.active


def test_looking_for_bills_filters_by_number_and_contact_and_can_limit_to_ours() -> None:
    page = INVOICE_OK.replace("}]}", '},{"Type":"ACCREC","InvoiceID":"sale"}]}')
    c, server, _ = connector(ok(page))
    got = c.find_bills("c-1", "INV-001", created_by_us=True)
    params = dict(server.requests[0].url.params)
    assert params == {"InvoiceNumbers": "INV-001", "ContactIDs": "c-1", "createdByMyApp": "true"}
    assert [b.bill_id for b in got] == ["inv-9"]  # the sales invoice is not a bill
    c2, server2, _ = connector(ok())
    c2.find_bills("c-1", "INV-001", created_by_us=False)
    assert "createdByMyApp" not in dict(server2.requests[0].url.params)


# ---- the draft-only guarantee ---------------------------------------------------------------


def test_the_adapter_can_only_read_and_create_drafts() -> None:
    c, server, _ = connector(ok(), ok('{"Contacts":[]}'), ok('{"Invoices":[]}'))
    c.create_draft_bill(bill(), "k")
    c.find_contacts("x", None)
    c.find_bills("c", "n", created_by_us=False)
    for req in server.requests:
        if req.method != "GET":
            assert (req.method, req.url.path) == ("PUT", "/api.xro/2.0/Invoices")
            assert json.loads(req.content)["Invoices"][0]["Status"] == "DRAFT"
        low = str(req.url).lower()
        assert "payment" not in low and "approve" not in low and "authorised" not in low


def test_the_adapter_has_no_approve_or_pay_method() -> None:
    names = {n.lower() for n in dir(XeroConnector) if not n.startswith("__")}
    assert not {
        n for n in names if any(w in n for w in ("approve", "pay", "authoris", "void", "delete"))
    }


# ---- decimals, never floats -----------------------------------------------------------------


def test_amounts_are_written_as_exact_decimal_numbers() -> None:
    assert dumps({"a": Decimal("1.10"), "b": [Decimal("0.07")], "c": None, "d": True, "e": 3}) == (
        '{"a":1.10,"b":[0.07],"c":null,"d":true,"e":3}'
    )
    assert dumps({"s": 'q"uote'}) == '{"s":"q\\"uote"}'


def test_a_float_is_refused_outright() -> None:
    with pytest.raises(TypeError):
        dumps({"a": 1.1})


def test_minor_units_and_decimals_convert_exactly() -> None:
    assert to_decimal(10005, "AUD") == Decimal("100.05")
    assert to_decimal(1500, "JPY") == Decimal("1500")
    assert to_minor(Decimal("100.05"), "AUD") == 10005
    assert (
        to_minor(100.05, "AUD") == 10005
    )  # a float that arrives is read by its text, not its bits
    with pytest.raises(ValueError):
        to_minor(Decimal("1.005"), "AUD")


# ---- connecting -----------------------------------------------------------------------------


def token_server(answer: httpx.Response) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return answer

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


TOKEN_OK = '{"access_token":"A","refresh_token":"R","expires_in":1800}'


def test_the_code_is_exchanged_with_the_pkce_verifier_and_basic_auth() -> None:
    client, seen = token_server(ok(TOKEN_OK))
    t = exchange_code(
        client, client_id="id", client_secret="sec", redirect_uri="https://app/cb", code="C",
        verifier="V",
    )  # fmt: skip
    assert (t.access_token, t.refresh_token) == ("A", "R") and t.expires_at > 0
    body = seen[0].content.decode()
    assert "grant_type=authorization_code" in body and "code_verifier=V" in body
    assert seen[0].headers["Authorization"].startswith("Basic ")


def test_a_public_client_sends_its_id_instead_of_a_secret() -> None:
    client, seen = token_server(ok(TOKEN_OK))
    exchange_code(
        client, client_id="id", client_secret="", redirect_uri="u", code="C", verifier="V"
    )
    assert "client_id=id" in seen[0].content.decode() and "Authorization" not in seen[0].headers


def test_refreshing_uses_the_refresh_token_and_reports_a_refusal_without_secrets() -> None:
    client, seen = token_server(ok(TOKEN_OK))
    assert make_refresher(client, "id", "sec")("R0").refresh_token == "R"
    assert "grant_type=refresh_token" in seen[0].content.decode()
    bad, _ = token_server(ok('{"error":"invalid_grant"}', 400))
    with pytest.raises(ProviderError) as err:
        make_refresher(bad, "id", "sec")("R0")
    assert err.value.failure is Failure.UNAUTHORISED and "R0" not in str(err.value)


@pytest.mark.parametrize("text", ["nope", "{}", '{"access_token":"A"}'])
def test_an_unreadable_token_answer_is_a_bad_response(text: str) -> None:
    client, _ = token_server(ok(text))
    with pytest.raises(ProviderError) as err:
        make_refresher(client, "id", "sec")("R0")
    assert err.value.failure is Failure.BAD_RESPONSE


def test_organisations_are_listed_from_the_connections_endpoint() -> None:
    client, seen = token_server(ok('[{"tenantId":"t1","tenantName":"Demo"},{"tenantId":"t2"}]'))
    assert list_organisations(client, "A") == [("t1", "Demo"), ("t2", "")]
    assert seen[0].headers["Authorization"] == "Bearer A"
    for answer in (ok("{}", 401), ok("not json")):
        c, _ = token_server(answer)
        with pytest.raises(ProviderError):
            list_organisations(c, "A")


def test_a_token_never_shows_in_a_repr() -> None:
    assert "secret-value" not in repr(Tokens("secret-value", "secret-refresh", 1))
