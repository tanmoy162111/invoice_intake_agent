"""Project B's hard rules, checked against the source itself: draft bills only, nothing that
approves, pays or moves money, no secret in the repository, no token in a log line."""

import re
from pathlib import Path

import pytest

from intake.connectors.accounting import AccountingConnector
from intake.connectors.mock import MockAccounting
from intake.connectors.xero import XeroConnector

SRC = Path(__file__).resolve().parents[2] / "src" / "intake"
REPO = Path(__file__).resolve().parents[4]
CONNECTOR_FILES = [
    *SRC.glob("connectors/*.py"),
    *SRC.glob("handoff/*.py"),
    SRC / "api/accounting.py",
]
FORBIDDEN_WORDS = ("approve_bill", "pay_bill", "make_payment", "submit_payment", "batchpayment")


def test_the_connector_interface_cannot_say_approve_or_pay() -> None:
    methods = {n for n in dir(AccountingConnector) if not n.startswith("_")}
    assert methods == {"find_contacts", "get_contact", "find_bills", "create_draft_bill"}


@pytest.mark.parametrize("cls", [XeroConnector, MockAccounting])
def test_no_adapter_has_a_method_that_could_approve_pay_or_void(cls: type) -> None:
    for name in dir(cls):
        low = name.lower()
        assert not any(
            w in low for w in ("approve", "pay", "authoris", "void", "delete", "submit")
        ), name


@pytest.mark.parametrize("path", CONNECTOR_FILES, ids=lambda p: p.name)
def test_no_source_file_calls_an_approval_or_payment_endpoint(path: Path) -> None:
    text = path.read_text().lower()
    for word in FORBIDDEN_WORDS:
        assert word not in text, f"{path.name} mentions {word}"
    # The one status ever written is DRAFT; AUTHORISED/PAID appear only as things to refuse or read.
    for match in re.finditer(r'"status"\s*:\s*"?(\w+)', path.read_text(), re.I):
        assert match.group(1) in ("DRAFT", "DRAFT_"), (path.name, match.group(0))
    assert "/payments" not in text and "/batchpayments" not in text and "/prepayments" not in text


def test_the_only_write_the_xero_adapter_makes_is_the_create_request() -> None:
    text = (SRC / "connectors/xero.py").read_text()
    verbs = re.findall(r'self\._send\(\s*"(\w+)"\s*,\s*([^,]+)', text)
    writes = [(v, p) for v, p in verbs if v != "GET"]
    assert writes == [("PUT", '"/Invoices"')]


def test_the_services_never_log_or_return_token_values() -> None:
    for path in CONNECTOR_FILES:
        text = path.read_text()
        assert not re.search(r"(logging|logger|print)\w*\(.*(token|secret)", text, re.I), path.name


def test_the_draft_only_sentence_is_in_the_interface_and_the_api() -> None:
    from intake.api.accounting import DRAFT_ONLY

    assert DRAFT_ONLY == (
        "Creates draft bills only. "
        "A human must review and approve bills in the accounting platform."
    )
    interface = (SRC / "connectors/accounting.py").read_text()
    assert "Creates draft bills only. A human must review and approve bills in the accounting" in (
        " ".join(interface.split())
    )


def test_nothing_secret_is_committed_and_the_env_template_has_only_placeholders() -> None:
    template = (REPO / ".env.example").read_text()
    for name in ("ACCOUNTING_PROVIDER", "ACCOUNTING_ENCRYPTION_KEY", "XERO_CLIENT_ID",
                 "XERO_CLIENT_SECRET", "XERO_REDIRECT_URI", "ACCOUNTING_TAX_TYPES"):  # fmt: skip
        match = re.search(rf"^{name}=(.*)$", template, re.M)
        assert match is not None, f"{name} missing from .env.example"
        assert match.group(1).strip() in ("", "mock", '{"1000": "INPUT"}', "{}"), name
    ignored = (REPO / ".gitignore").read_text()
    assert ".env" in ignored and "data/real" in ignored or "data/inbox" in ignored
