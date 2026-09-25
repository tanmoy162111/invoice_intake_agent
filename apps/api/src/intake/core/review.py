"""Rules for what a reviewer may do (playbook M8). Pure: facts in, a problem or nothing out.

An invoice can be approved only when every one of its exceptions is closed (resolved or dismissed),
and a `block` exception needs a written note to close. A correction is normalized here with the same
parsers that read the document, so a corrected value means exactly what a read one would.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from intake.core.exceptions import ExceptionCode, Severity
from intake.core.extraction import _parse_currency as parse_currency
from intake.core.extraction import _parse_date as parse_date
from intake.core.extraction import _parse_money as parse_money
from intake.core.extraction import _parse_text as parse_text
from intake.core.money import Money
from intake.core.statuses import ExceptionStatus, InvoiceStatus

NOTE_MAX = 2000
VALUE_MAX = 500
_OPEN_STATUSES = frozenset({InvoiceStatus.CLEARED, InvoiceStatus.NEEDS_REVIEW})


class ReviewProblem(StrEnum):
    WRONG_STATUS = "WRONG_STATUS"
    OPEN_EXCEPTIONS = "OPEN_EXCEPTIONS"
    NOTE_REQUIRED = "NOTE_REQUIRED"
    NOTE_TOO_LONG = "NOTE_TOO_LONG"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    VALUE_REQUIRED = "VALUE_REQUIRED"
    INVALID_VALUE = "INVALID_VALUE"
    CURRENCY_NEEDED = "CURRENCY_NEEDED"


@dataclass(frozen=True)
class ExceptionState:
    code: ExceptionCode
    severity: Severity
    status: ExceptionStatus


def approval_problem(
    status: InvoiceStatus, exceptions: Sequence[ExceptionState]
) -> ReviewProblem | None:
    if status not in _OPEN_STATUSES:
        return ReviewProblem.WRONG_STATUS
    if any(e.status is ExceptionStatus.OPEN for e in exceptions):
        return ReviewProblem.OPEN_EXCEPTIONS
    return None


def _note_problem(note: str | None, *, required: bool) -> ReviewProblem | None:
    text = (note or "").strip()
    if len(text) > NOTE_MAX:
        return ReviewProblem.NOTE_TOO_LONG
    if required and not text:
        return ReviewProblem.NOTE_REQUIRED
    return None


def resolution_problem(severity: Severity, note: str | None) -> ReviewProblem | None:
    """Closing an exception: a block exception needs a note, and no note is enormous."""
    return _note_problem(note, required=severity is Severity.BLOCK)


def reject_problem(status: InvoiceStatus, reason: str | None) -> ReviewProblem | None:
    if status not in _OPEN_STATUSES:
        return ReviewProblem.WRONG_STATUS
    return _note_problem(reason, required=True)


def correct_problem(status: InvoiceStatus) -> ReviewProblem | None:
    return None if status in _OPEN_STATUSES else ReviewProblem.WRONG_STATUS


def request_info_problem(status: InvoiceStatus, note: str | None) -> ReviewProblem | None:
    if status not in _OPEN_STATUSES:
        return ReviewProblem.WRONG_STATUS
    return _note_problem(note, required=False)


# ---- corrections ------------------------------------------------------------------------------

# field -> the invoice column it fills (None: kept only with the extracted fields).
_COLUMN: dict[str, str | None] = {
    "supplier_name": "supplier_name",
    "supplier_tax_id": None,
    "supplier_address": None,
    "invoice_number": "invoice_number",
    "invoice_date": "invoice_date",
    "due_date": "due_date",
    "po_number": "po_number",
    "currency": "currency",
    "subtotal": "subtotal_minor",
    "tax_total": "tax_minor",
    "total": "total_minor",
    "payment_terms": "payment_terms",
}
CORRECTABLE_FIELDS = frozenset(_COLUMN)  # the bank account is never edited, only read
_MONEY = frozenset({"subtotal", "tax_total", "total"})
_DATES = frozenset({"invoice_date", "due_date"})
_OPTIONAL = frozenset(
    {"po_number", "due_date", "payment_terms", "supplier_tax_id", "supplier_address"}
)


@dataclass(frozen=True)
class Correction:
    field: str
    text: str | None  # the normalized form stored with the field
    column: str | None  # the invoice column to set, if any
    value: object | None  # what goes in that column


def normalize_correction(field: str, raw: str, currency: str | None) -> Correction | ReviewProblem:
    if field not in CORRECTABLE_FIELDS:
        return ReviewProblem.UNKNOWN_FIELD
    column = _COLUMN[field]
    text = (raw or "").strip()
    if len(text) > VALUE_MAX:
        return ReviewProblem.INVALID_VALUE
    if not text:
        if field in _OPTIONAL:
            return Correction(field, None, column, None)
        return ReviewProblem.VALUE_REQUIRED
    if field in _MONEY:
        if currency is None:
            return ReviewProblem.CURRENCY_NEEDED
        parsed = parse_money(text, currency)
    elif field in _DATES:
        parsed = parse_date(text, None)  # a day and month that could swap must be typed as ISO
    elif field == "currency":
        parsed = parse_currency(text, None)
    else:
        parsed = parse_text(field, text)
    if parsed.error is not None or parsed.typed is None or parsed.text is None:
        return ReviewProblem.INVALID_VALUE
    value = parsed.typed.minor if isinstance(parsed.typed, Money) else parsed.typed
    return Correction(field, parsed.text, column, value)


# ---- why a field is doubtful ------------------------------------------------------------------

_NORMALIZE_WORDS = {
    "AMBIGUOUS_DATE": "the date could be read two ways",
    "AMBIGUOUS_NUMBER": "the number could be read two ways",
    "AMBIGUOUS_CURRENCY": "the currency symbol could mean several currencies",
    "UNSUPPORTED_CURRENCY": "the currency is not supported",
    "NO_CURRENCY": "the amount has no currency to read it in",
    "UNPARSEABLE": "the value could not be read as a number, date or code",
}
_SUPPORT_SIGNALS = ("text_layer", "rule", "master_data")


def confidence_reason(
    confidence: Decimal | None, signals: Mapping[str, object], minimum: Decimal
) -> str | None:
    """Plain words for why a field is doubtful, or None when it is confident enough."""
    if confidence is None:
        return "it was not read"
    if confidence >= minimum:
        return None
    if "normalize_error" in signals:
        return _NORMALIZE_WORDS.get(
            str(signals["normalize_error"]), "the value could not be normalized"
        )
    if signals.get("missing") is True:
        return "it was not found on the document"
    if signals.get("text_layer") is False:
        return "it does not match the document's text layer"
    if signals.get("rule") is False:
        return "it disagrees with another value on the invoice (a sum or a rate)"
    if signals.get("master_data") is False:
        return "it does not match the supplier records"
    present = [k for k in _SUPPORT_SIGNALS if k in signals]
    if present and not any(signals[k] is True for k in present):
        return "nothing confirms it: no text layer, rule or supplier record supports it"
    if signals.get("self_confidence") in ("low", "medium"):
        return "the reader was not sure of it"
    return "its confidence is below the minimum"


# ---- a reviewer's earlier decision is kept ----------------------------------------------------


@dataclass(frozen=True)
class PriorDecision:
    code: ExceptionCode
    explanation: str
    status: ExceptionStatus
    decided_by: str | None


def is_carried_over(priors: Sequence[PriorDecision], code: ExceptionCode, explanation: str) -> bool:
    """A person already closed this exact exception (same code and same words, so same numbers):
    after a re-check it is not raised again. One closed by the system, or still open, never
    counts."""
    return any(
        p.code is code
        and p.explanation == explanation
        and p.status in (ExceptionStatus.DISMISSED, ExceptionStatus.RESOLVED)
        and p.decided_by not in (None, "system")
        for p in priors
    )
