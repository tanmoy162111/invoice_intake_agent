"""Xero adapter: finds contacts and creates DRAFT supplier bills (type ACCPAY). Nothing else.

Creates draft bills only. A human must review and approve bills in the accounting platform.

Endpoints, fields and scopes are from Xero's published Accounting API OpenAPI description
(XeroAPI/Xero-OpenAPI, v19.1.0); see docs/connectors.md for what is and is not verified. There is
no method here, and no request below, that approves, pays or voids a bill. Amounts are built from
integer minor units and sent as exact decimal numbers; nothing passes through a float.
"""

import json
import time
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import httpx

from intake.connectors.accounting import ProviderError
from intake.connectors.tokens import Refresher, TokenProvider, Tokens
from intake.core.accounting import (
    DRAFT,
    BillDraft,
    ContactCandidate,
    Failure,
    ReturnedBill,
)
from intake.core.money import CURRENCY_EXPONENT

API = "https://api.xero.com/api.xro/2.0"
TOKEN_URL = "https://identity.xero.com/connect/token"
AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
CONNECTIONS_URL = "https://api.xero.com/connections"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def to_decimal(minor: int, currency: str) -> Decimal:
    return Decimal(minor).scaleb(-CURRENCY_EXPONENT[currency])


def to_minor(value: object, currency: str) -> int:
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    scaled = d.scaleb(CURRENCY_EXPONENT[currency])
    if scaled != scaled.to_integral_value():
        raise ValueError("amount has more decimals than the currency allows")
    return int(scaled)


def dumps(obj: object) -> str:
    """JSON with exact decimal numbers (the standard library cannot write a Decimal as a number)."""
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, str):
        return json.dumps(obj)
    if isinstance(obj, Mapping):
        return "{" + ",".join(f"{json.dumps(k)}:{dumps(v)}" for k, v in obj.items()) + "}"
    if isinstance(obj, list | tuple):
        return "[" + ",".join(dumps(v) for v in obj) + "]"
    raise TypeError(f"cannot write {type(obj).__name__} as JSON")


def bill_body(bill: BillDraft) -> dict[str, Any]:
    """The request body for one new bill: ACCPAY, DRAFT, amounts exclusive of tax."""
    lines: list[dict[str, Any]] = []
    for ln in bill.lines:
        item: dict[str, Any] = {
            "Description": ln.description,
            "LineAmount": to_decimal(ln.amount_minor, bill.currency),
            "TaxType": ln.tax_type,
        }
        if ln.quantity is not None and ln.unit_amount_minor is not None:
            item["Quantity"] = ln.quantity
            item["UnitAmount"] = to_decimal(ln.unit_amount_minor, bill.currency)
        if ln.account_code is not None:
            item["AccountCode"] = ln.account_code
        lines.append(item)
    invoice: dict[str, Any] = {
        "Type": "ACCPAY",
        "Status": DRAFT,
        "Contact": {"ContactID": bill.contact_id},
        "InvoiceNumber": bill.invoice_number,
        "Date": bill.invoice_date.isoformat(),
        "CurrencyCode": bill.currency,
        "LineAmountTypes": "Exclusive",
        "LineItems": lines,
    }
    if bill.due_date is not None:
        invoice["DueDate"] = bill.due_date.isoformat()
    return {"Invoices": [invoice]}


class XeroConnector:
    provider = "xero"

    def __init__(
        self, organisation_id: str, tokens: TokenProvider, client: httpx.Client | None = None
    ) -> None:
        self.organisation_id = organisation_id
        self._tokens = tokens
        self._http = client or httpx.Client(timeout=TIMEOUT)

    # ---- requests ----------------------------------------------------------------------------
    def _send(
        self, method: str, path: str, *, params: dict[str, str] | None = None,
        content: str | None = None, extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        token = self._tokens.access_token()
        for attempt in (1, 2):
            headers = {
                "Authorization": f"Bearer {token}", "xero-tenant-id": self.organisation_id,
                "Accept": "application/json", **(extra or {}),
            }  # fmt: skip
            if content is not None:
                headers["Content-Type"] = "application/json"
            try:
                resp = self._http.request(
                    method, f"{API}{path}", params=params, headers=headers,
                    content=content.encode() if content is not None else None,
                )  # fmt: skip
            except httpx.ConnectError:
                raise ProviderError(Failure.CONNECT_FAILED, detail="could not connect") from None
            except httpx.ConnectTimeout:
                raise ProviderError(Failure.CONNECT_FAILED, detail="connect timed out") from None
            except httpx.TimeoutException:
                raise ProviderError(Failure.TIMEOUT, detail="no answer in time") from None
            except httpx.TransportError:
                raise ProviderError(Failure.CONNECTION_LOST, detail="connection lost") from None
            if resp.status_code == 401 and attempt == 1:
                token = self._tokens.refresh(token)  # not processed: safe to send once more
                continue
            return self._read(resp)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _read(resp: httpx.Response) -> dict[str, Any]:
        code = resp.status_code
        if code == 429:
            raw = resp.headers.get("Retry-After", "")
            wait = int(raw) if raw.isdigit() else None
            raise ProviderError(Failure.RATE_LIMITED, retry_after_s=wait, detail="rate limited")
        if code == 401:
            raise ProviderError(Failure.UNAUTHORISED, detail="not authorised")
        if code == 403:
            raise ProviderError(Failure.FORBIDDEN, detail="permission missing")
        if code == 409:
            raise ProviderError(Failure.CONFLICT, detail="conflict")
        if 400 <= code < 500:
            raise ProviderError(Failure.REJECTED, detail=f"rejected (HTTP {code})")
        if code >= 500:
            raise ProviderError(Failure.SERVER_ERROR, detail=f"server error (HTTP {code})")
        try:
            body = json.loads(resp.text, parse_float=Decimal)
        except ValueError:
            raise ProviderError(Failure.BAD_RESPONSE, detail="answer is not JSON") from None
        if not isinstance(body, dict):
            raise ProviderError(Failure.BAD_RESPONSE, detail="unexpected answer")
        return body

    # ---- the connector interface -----------------------------------------------------------------
    def find_contacts(self, name: str, tax_id: str | None) -> list[ContactCandidate]:
        found: dict[str, ContactCandidate] = {}
        for term in [t for t in (name, tax_id) if t]:
            body = self._send("GET", "/Contacts", params={"searchTerm": term})
            for raw in _list(body, "Contacts"):
                c = _contact(raw)
                found.setdefault(c.contact_id, c)
        return list(found.values())

    def get_contact(self, contact_id: str) -> ContactCandidate | None:
        try:
            body = self._send("GET", f"/Contacts/{contact_id}")
        except ProviderError as exc:
            if exc.failure is Failure.REJECTED and "HTTP 404" in exc.detail:
                return None
            raise
        contacts = _list(body, "Contacts")
        return _contact(contacts[0]) if contacts else None

    def find_bills(
        self, contact_id: str, invoice_number: str, *, created_by_us: bool
    ) -> list[ReturnedBill]:
        params = {"InvoiceNumbers": invoice_number, "ContactIDs": contact_id}
        if created_by_us:
            params["createdByMyApp"] = "true"
        body = self._send("GET", "/Invoices", params=params)
        return [_bill(raw) for raw in _list(body, "Invoices") if raw.get("Type") == "ACCPAY"]

    def create_draft_bill(self, bill: BillDraft, idempotency_key: str) -> ReturnedBill:
        body = self._send(
            "PUT", "/Invoices", params={"summarizeErrors": "true"}, content=dumps(bill_body(bill)),
            extra={"Idempotency-Key": idempotency_key},
        )  # fmt: skip
        invoices = _list(body, "Invoices")
        if len(invoices) != 1:
            raise ProviderError(Failure.BAD_RESPONSE, detail="expected exactly one bill back")
        return _bill(invoices[0])


def _list(body: dict[str, Any], key: str) -> list[dict[str, Any]]:
    items = body.get(key)
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ProviderError(Failure.BAD_RESPONSE, detail=f"no {key} in the answer")
    return items


def _contact(raw: dict[str, Any]) -> ContactCandidate:
    try:
        return ContactCandidate(
            contact_id=str(raw["ContactID"]),
            name=str(raw.get("Name") or ""),
            tax_number=str(raw["TaxNumber"]) if raw.get("TaxNumber") else None,
            active=raw.get("ContactStatus", "ACTIVE") == "ACTIVE",
        )
    except KeyError:
        raise ProviderError(Failure.BAD_RESPONSE, detail="contact without an id") from None


def _bill(raw: dict[str, Any]) -> ReturnedBill:
    try:
        currency = str(raw["CurrencyCode"])
        return ReturnedBill(
            bill_id=str(raw["InvoiceID"]),
            status=str(raw["Status"]),
            contact_id=str(raw["Contact"]["ContactID"]),
            currency=currency,
            subtotal_minor=to_minor(raw["SubTotal"], currency),
            tax_minor=to_minor(raw["TotalTax"], currency),
            total_minor=to_minor(raw["Total"], currency),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise ProviderError(Failure.BAD_RESPONSE, detail="bill in an unexpected shape") from None


# ---- OAuth token calls (no bill or contact is touched) -------------------------------------------


def _token_call(client: httpx.Client, data: dict[str, str], id_: str, secret: str) -> Tokens:
    try:
        if secret:
            resp = client.post(TOKEN_URL, data=data, auth=(id_, secret))
        else:  # a public client proves itself with PKCE alone
            resp = client.post(TOKEN_URL, data={**data, "client_id": id_})
    except httpx.TransportError:
        raise ProviderError(Failure.CONNECT_FAILED, detail="token endpoint unreachable") from None
    if resp.status_code != 200:
        raise ProviderError(
            Failure.UNAUTHORISED, detail=f"token request refused (HTTP {resp.status_code})"
        )
    try:
        d = resp.json()
        return Tokens(
            str(d["access_token"]), str(d["refresh_token"]), int(time.time()) + int(d["expires_in"])
        )
    except (ValueError, KeyError, TypeError):
        raise ProviderError(Failure.BAD_RESPONSE, detail="token answer unreadable") from None


def exchange_code(
    client: httpx.Client, *, client_id: str, client_secret: str, redirect_uri: str, code: str,
    verifier: str,
) -> Tokens:  # fmt: skip
    return _token_call(
        client,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
         "code_verifier": verifier},
        client_id, client_secret,
    )  # fmt: skip


def make_refresher(client: httpx.Client, client_id: str, client_secret: str) -> Refresher:
    def refresh(refresh_token: str) -> Tokens:
        return _token_call(
            client, {"grant_type": "refresh_token", "refresh_token": refresh_token},
            client_id, client_secret,
        )  # fmt: skip

    return refresh


def list_organisations(client: httpx.Client, access_token: str) -> list[tuple[str, str]]:
    """(organisation id, name) for every organisation this sign-in granted access to."""
    try:
        resp = client.get(CONNECTIONS_URL, headers={"Authorization": f"Bearer {access_token}"})
        rows = resp.json() if resp.status_code == 200 else None
    except (httpx.TransportError, ValueError):
        rows = None
    if not isinstance(rows, list):
        raise ProviderError(Failure.BAD_RESPONSE, detail="organisations could not be listed")
    return [(str(r["tenantId"]), str(r.get("tenantName") or "")) for r in rows if "tenantId" in r]
