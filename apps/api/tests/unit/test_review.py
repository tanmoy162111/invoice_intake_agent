from datetime import date
from decimal import Decimal

import pytest

from intake.core.exceptions import ExceptionCode, Severity
from intake.core.review import (
    CORRECTABLE_FIELDS,
    ExceptionState,
    PriorDecision,
    ReviewProblem,
    approval_problem,
    confidence_reason,
    correct_problem,
    is_carried_over,
    normalize_correction,
    reject_problem,
    request_info_problem,
    resolution_problem,
)
from intake.core.statuses import ExceptionStatus, InvoiceStatus

S = InvoiceStatus
X = ExceptionStatus


def exc(severity: Severity, status: ExceptionStatus = X.OPEN) -> ExceptionState:
    return ExceptionState(ExceptionCode.PRICE_VARIANCE, severity, status)


# ---- approving --------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [S.CLEARED, S.NEEDS_REVIEW])
def test_an_invoice_with_every_exception_closed_can_be_approved(status: InvoiceStatus) -> None:
    closed = [exc(Severity.BLOCK, X.RESOLVED), exc(Severity.REVIEW, X.DISMISSED)]
    assert approval_problem(status, closed) is None
    assert approval_problem(status, []) is None


@pytest.mark.parametrize("severity", list(Severity))
def test_any_open_exception_blocks_approval(severity: Severity) -> None:
    assert approval_problem(S.NEEDS_REVIEW, [exc(severity)]) is ReviewProblem.OPEN_EXCEPTIONS


@pytest.mark.parametrize(
    "status",
    [
        S.RECEIVED,
        S.EXTRACTING,
        S.EXTRACTED,
        S.CHECKING,
        S.APPROVED,
        S.REJECTED,
        S.EXPORTED,
        S.FAILED,
    ],
)
def test_only_a_cleared_or_needs_review_invoice_can_be_approved(status: InvoiceStatus) -> None:
    assert approval_problem(status, []) is ReviewProblem.WRONG_STATUS


def test_the_wrong_status_is_reported_before_open_exceptions() -> None:
    assert approval_problem(S.FAILED, [exc(Severity.BLOCK)]) is ReviewProblem.WRONG_STATUS


# ---- notes ------------------------------------------------------------------------------------


def test_a_block_exception_needs_a_note_to_close() -> None:
    assert resolution_problem(Severity.BLOCK, None) is ReviewProblem.NOTE_REQUIRED
    assert resolution_problem(Severity.BLOCK, "   ") is ReviewProblem.NOTE_REQUIRED
    assert resolution_problem(Severity.BLOCK, "Phoned the supplier on file") is None


@pytest.mark.parametrize("severity", [Severity.REVIEW, Severity.INFO])
def test_other_exceptions_need_no_note(severity: Severity) -> None:
    assert resolution_problem(severity, None) is None
    assert resolution_problem(severity, "") is None


def test_a_note_cannot_be_enormous() -> None:
    assert resolution_problem(Severity.REVIEW, "x" * 2000) is None
    assert resolution_problem(Severity.REVIEW, "x" * 2001) is ReviewProblem.NOTE_TOO_LONG
    assert resolution_problem(Severity.BLOCK, "x" * 2001) is ReviewProblem.NOTE_TOO_LONG


@pytest.mark.parametrize("status", [S.CLEARED, S.NEEDS_REVIEW])
def test_rejecting_needs_a_reason(status: InvoiceStatus) -> None:
    assert reject_problem(status, "Duplicate of INV-1") is None
    assert reject_problem(status, "  ") is ReviewProblem.NOTE_REQUIRED
    assert reject_problem(status, None) is ReviewProblem.NOTE_REQUIRED
    assert reject_problem(status, "x" * 2001) is ReviewProblem.NOTE_TOO_LONG


@pytest.mark.parametrize("status", [S.CHECKING, S.APPROVED, S.REJECTED, S.FAILED, S.EXPORTED])
def test_only_an_open_invoice_can_be_rejected_corrected_or_queried(status: InvoiceStatus) -> None:
    assert reject_problem(status, "reason") is ReviewProblem.WRONG_STATUS
    assert correct_problem(status) is ReviewProblem.WRONG_STATUS
    assert request_info_problem(status, None) is ReviewProblem.WRONG_STATUS


def test_correcting_and_querying_are_allowed_on_an_open_invoice() -> None:
    for status in (S.CLEARED, S.NEEDS_REVIEW):
        assert correct_problem(status) is None
        assert request_info_problem(status, "Please send the PO") is None
    assert request_info_problem(S.NEEDS_REVIEW, "x" * 2001) is ReviewProblem.NOTE_TOO_LONG


# ---- corrections ------------------------------------------------------------------------------


def test_the_correctable_fields_are_the_header_fields_but_not_the_bank_account() -> None:
    assert "total" in CORRECTABLE_FIELDS and "invoice_date" in CORRECTABLE_FIELDS
    assert "supplier_bank_account" not in CORRECTABLE_FIELDS
    assert not any(f.startswith("line.") for f in CORRECTABLE_FIELDS)


def test_a_money_correction_becomes_minor_units_in_the_invoice_currency() -> None:
    c = normalize_correction("total", "1,234.50", "USD")
    assert not isinstance(c, ReviewProblem)
    assert (c.text, c.column, c.value) == ("123450", "total_minor", 123450)
    jpy = normalize_correction("subtotal", "5000", "JPY")
    assert (
        not isinstance(jpy, ReviewProblem) and jpy.value == 5000 and jpy.column == "subtotal_minor"
    )
    tax = normalize_correction("tax_total", "0.00", "EUR")
    assert not isinstance(tax, ReviewProblem) and tax.column == "tax_minor" and tax.value == 0


def test_a_money_correction_needs_a_known_currency_first() -> None:
    assert normalize_correction("total", "10.00", None) is ReviewProblem.CURRENCY_NEEDED


@pytest.mark.parametrize("bad", ["abc", "1.2.3", "12,50,00x"])
def test_an_unreadable_amount_is_refused(bad: str) -> None:
    assert normalize_correction("total", bad, "USD") is ReviewProblem.INVALID_VALUE


def test_a_date_correction_accepts_iso_and_refuses_ambiguous_days() -> None:
    ok = normalize_correction("invoice_date", "2026-05-31", "USD")
    assert not isinstance(ok, ReviewProblem)
    assert (ok.text, ok.column, ok.value) == ("2026-05-31", "invoice_date", date(2026, 5, 31))
    assert normalize_correction("due_date", "03/04/2026", "USD") is ReviewProblem.INVALID_VALUE
    assert normalize_correction("invoice_date", "31 Feb 2026", "USD") is ReviewProblem.INVALID_VALUE


def test_a_currency_correction_is_normalized_to_its_code() -> None:
    c = normalize_correction("currency", "eur", None)
    assert not isinstance(c, ReviewProblem)
    assert (c.text, c.column, c.value) == ("EUR", "currency", "EUR")
    assert normalize_correction("currency", "XXX", None) is ReviewProblem.INVALID_VALUE


def test_text_corrections_are_tidied() -> None:
    c = normalize_correction("supplier_name", "  Acme   Supply  Co. ", "USD")
    assert not isinstance(c, ReviewProblem)
    assert (c.column, c.value) == ("supplier_name", "Acme Supply Co.")
    assert c.text == "acme supply co."  # the form used for matching
    tid = normalize_correction("supplier_tax_id", " ab-123 ", "USD")
    assert not isinstance(tid, ReviewProblem) and tid.column is None and tid.text == "AB-123"


@pytest.mark.parametrize("field", ["po_number", "due_date", "payment_terms", "supplier_tax_id"])
def test_an_optional_field_can_be_cleared(field: str) -> None:
    c = normalize_correction(field, "  ", "USD")
    assert not isinstance(c, ReviewProblem) and c.value is None and c.text is None


@pytest.mark.parametrize(
    "field", ["total", "invoice_number", "invoice_date", "currency", "supplier_name"]
)
def test_an_important_field_cannot_be_blanked(field: str) -> None:
    assert normalize_correction(field, "", "USD") is ReviewProblem.VALUE_REQUIRED


@pytest.mark.parametrize("field", ["supplier_bank_account", "line.1.quantity", "nonsense", ""])
def test_a_field_that_cannot_be_corrected_is_refused(field: str) -> None:
    assert normalize_correction(field, "x", "USD") is ReviewProblem.UNKNOWN_FIELD


def test_a_correction_value_cannot_be_enormous() -> None:
    assert normalize_correction("supplier_name", "x" * 501, "USD") is ReviewProblem.INVALID_VALUE


# ---- why a field is doubtful ------------------------------------------------------------------

MIN = Decimal("0.8")


def test_a_confident_field_has_no_reason() -> None:
    assert confidence_reason(Decimal("0.9"), {}, MIN) is None
    assert confidence_reason(MIN, {}, MIN) is None


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        ({"normalize_error": "AMBIGUOUS_DATE"}, "the date could be read two ways"),
        (
            {"normalize_error": "UNPARSEABLE"},
            "the value could not be read as a number, date or code",
        ),
        ({"normalize_error": "SOMETHING_NEW"}, "the value could not be normalized"),
        ({"missing": True}, "it was not found on the document"),
        ({"text_layer": False}, "it does not match the document's text layer"),
        ({"rule": False}, "it disagrees with another value on the invoice (a sum or a rate)"),
        ({"master_data": False}, "it does not match the supplier records"),
        (
            {"text_layer": None, "rule": None, "master_data": None, "self_confidence": "high"},
            "nothing confirms it: no text layer, rule or supplier record supports it",
        ),
        ({"text_layer": True, "self_confidence": "low"}, "the reader was not sure of it"),
        ({}, "its confidence is below the minimum"),
    ],
)
def test_the_reason_a_field_is_doubtful_is_stated_in_plain_words(
    signals: dict[str, object], expected: str
) -> None:
    assert confidence_reason(Decimal("0.5"), signals, MIN) == expected


def test_a_field_that_was_not_read_says_so() -> None:
    assert confidence_reason(None, {}, MIN) == "it was not read"


# ---- a reviewer's earlier decision is kept ----------------------------------------------------


def prior(status: ExceptionStatus, by: str | None = "reviewer", text: str = "T") -> PriorDecision:
    return PriorDecision(ExceptionCode.TOTAL_MISMATCH, text, status, by)


@pytest.mark.parametrize("status", [X.DISMISSED, X.RESOLVED])
def test_an_identical_exception_a_person_closed_is_not_raised_again(
    status: ExceptionStatus,
) -> None:
    assert is_carried_over([prior(status)], ExceptionCode.TOTAL_MISMATCH, "T")


def test_a_different_explanation_or_code_is_raised_again() -> None:
    assert not is_carried_over([prior(X.DISMISSED)], ExceptionCode.TOTAL_MISMATCH, "other numbers")
    assert not is_carried_over([prior(X.DISMISSED)], ExceptionCode.TAX_MISMATCH, "T")


def test_an_open_or_system_closed_exception_is_never_carried_over() -> None:
    assert not is_carried_over([prior(X.OPEN, by=None)], ExceptionCode.TOTAL_MISMATCH, "T")
    assert not is_carried_over([prior(X.RESOLVED, by="system")], ExceptionCode.TOTAL_MISMATCH, "T")
    assert not is_carried_over([prior(X.DISMISSED, by=None)], ExceptionCode.TOTAL_MISMATCH, "T")
    assert not is_carried_over([], ExceptionCode.TOTAL_MISMATCH, "T")
