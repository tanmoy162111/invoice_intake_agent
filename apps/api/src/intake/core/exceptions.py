"""Exception taxonomy (playbook §7): one enum, with an explanation template and fix per code.

Mirror any change here in docs/exception-taxonomy.md.
"""

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    INFO = "info"  # shown, does not stop the invoice
    REVIEW = "review"  # needs a person
    BLOCK = "block"  # needs a person and an explicit resolution note


class ExceptionCode(StrEnum):
    UNREADABLE_DOCUMENT = "UNREADABLE_DOCUMENT"
    LOW_CONFIDENCE_FIELD = "LOW_CONFIDENCE_FIELD"
    LINE_MATH_MISMATCH = "LINE_MATH_MISMATCH"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    TAX_MISMATCH = "TAX_MISMATCH"
    INVALID_DATE = "INVALID_DATE"
    UNKNOWN_SUPPLIER = "UNKNOWN_SUPPLIER"
    BANK_DETAILS_CHANGED = "BANK_DETAILS_CHANGED"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    NO_PO = "NO_PO"
    PO_NOT_FOUND = "PO_NOT_FOUND"
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QTY_VARIANCE = "QTY_VARIANCE"
    RECEIPT_MISSING = "RECEIPT_MISSING"
    QTY_NOT_RECEIVED = "QTY_NOT_RECEIVED"
    PO_OVERBILLED = "PO_OVERBILLED"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    ABOVE_APPROVAL_LIMIT = "ABOVE_APPROVAL_LIMIT"


@dataclass(frozen=True)
class ExceptionSpec:
    code: ExceptionCode
    severity: Severity
    explanation_template: str
    suggested_fix: str
    sample_params: dict[str, str]


def _spec(
    code: ExceptionCode,
    severity: Severity,
    template: str,
    fix: str,
    sample: dict[str, str],
) -> ExceptionSpec:
    return ExceptionSpec(code, severity, template, fix, sample)


_C = ExceptionCode
_S = Severity

SPECS: dict[ExceptionCode, ExceptionSpec] = {
    s.code: s
    for s in [
        _spec(
            _C.UNREADABLE_DOCUMENT,
            _S.BLOCK,
            "The file couldn't be read clearly (page {page} is blank or too blurry).",
            "Ask the supplier for a clearer copy",
            {"page": "2"},
        ),
        _spec(
            _C.LOW_CONFIDENCE_FIELD,
            _S.REVIEW,
            "We're not sure about the {field}: {reason}.",
            "Check the highlighted field and confirm or correct it",
            {"field": "total", "reason": "it wasn't found in the document's text layer"},
        ),
        _spec(
            _C.LINE_MATH_MISMATCH,
            _S.REVIEW,
            "Line {line}: {qty} × {unit_price} should be {expected}, "
            "but the invoice says {actual}.",
            "Confirm with the supplier; possibly a typo",
            {
                "line": "3",
                "qty": "12",
                "unit_price": "USD 45.00",
                "expected": "USD 540.00",
                "actual": "USD 504.00",
            },
        ),
        _spec(
            _C.TOTAL_MISMATCH,
            _S.REVIEW,
            "The lines add up to {expected}, but the invoice total is {actual}.",
            "Ask the supplier for a corrected invoice",
            {"expected": "USD 2,140.00", "actual": "USD 2,410.00"},
        ),
        _spec(
            _C.TAX_MISMATCH,
            _S.REVIEW,
            "Tax is {actual}, but {rate} of {base} is {expected}.",
            "Check the tax rate for this supplier",
            {
                "actual": "USD 318.00",
                "rate": "15%",
                "base": "USD 2,140.00",
                "expected": "USD 321.00",
            },
        ),
        _spec(
            _C.INVALID_DATE,
            _S.REVIEW,
            "The {field} is invalid: {reason}.",
            "Confirm the date with the supplier",
            {"field": "invoice date", "reason": "it is 3 months in the future"},
        ),
        _spec(
            _C.UNKNOWN_SUPPLIER,
            _S.REVIEW,
            "'{name}' doesn't match any known supplier. Closest match: '{closest}' ({score}%).",
            "Link to an existing supplier or create a new one",
            {"name": "Acme Supplies Ltd", "closest": "ACME Supply Co.", "score": "82"},
        ),
        _spec(
            _C.BANK_DETAILS_CHANGED,
            _S.BLOCK,
            "The bank account on this invoice is different from the one on file for {supplier}.",
            "Verify by phone using a known contact before approving. "
            "This is a common fraud pattern",
            {"supplier": "Acme Supply Co."},
        ),
        _spec(
            _C.POSSIBLE_DUPLICATE,
            _S.BLOCK,
            "This looks like invoice {other} from the same supplier, "
            "received {days} days ago, for the same amount.",
            "Compare them side by side; reject if duplicate",
            {"other": "INV-1043", "days": "4"},
        ),
        _spec(
            _C.NO_PO,
            _S.REVIEW,
            "This invoice has no PO number, and no open PO for this supplier matches the amount.",
            "Ask the requester for the PO, or approve as a non-PO invoice",
            {},
        ),
        _spec(
            _C.PO_NOT_FOUND,
            _S.REVIEW,
            "{po} isn't in the system.",
            "Check for a typo or ask purchasing",
            {"po": "PO-7781"},
        ),
        _spec(
            _C.PRICE_VARIANCE,
            _S.REVIEW,
            "Line {line} is billed at {actual} per unit, but the PO says {expected} "
            "({variance}, limit {limit}).",
            "Accept the variance, or ask the supplier to correct it",
            {
                "line": "2",
                "actual": "USD 48.00",
                "expected": "USD 45.00",
                "variance": "+6.7%",
                "limit": "2%",
            },
        ),
        _spec(
            _C.QTY_VARIANCE,
            _S.REVIEW,
            "Line {line} bills {actual} units, but the PO line is for {expected}.",
            "Ask the supplier or purchasing",
            {"line": "1", "actual": "120", "expected": "100"},
        ),
        _spec(
            _C.RECEIPT_MISSING,
            _S.REVIEW,
            "Nothing has been received yet for {po}.",
            "Wait for the goods receipt or confirm delivery with the requester",
            {"po": "PO-7781"},
        ),
        _spec(
            _C.QTY_NOT_RECEIVED,
            _S.REVIEW,
            "Invoice bills {billed} units; only {received} have been received.",
            "Hold, or pay partially once the rest arrives",
            {"billed": "100", "received": "80"},
        ),
        _spec(
            _C.PO_OVERBILLED,
            _S.BLOCK,
            "Invoices against {po} now total {billed}, which is more than the PO total of {total}.",
            "Stop and check with purchasing",
            {"po": "PO-7781", "billed": "USD 10,450.00", "total": "USD 9,800.00"},
        ),
        _spec(
            _C.CURRENCY_MISMATCH,
            _S.REVIEW,
            "The invoice is in {invoice_currency}, but the PO is in {po_currency}.",
            "Confirm the currency with the supplier",
            {"invoice_currency": "EUR", "po_currency": "USD"},
        ),
        _spec(
            _C.ABOVE_APPROVAL_LIMIT,
            _S.REVIEW,
            "The total of {total} is above the {limit} auto-approval limit.",
            "Needs manager approval",
            {"total": "USD 25,000.00", "limit": "USD 10,000.00"},
        ),
    ]
}


def explain(code: ExceptionCode, **params: str) -> str:
    """Render the explanation for a code. A missing parameter raises KeyError."""
    return SPECS[code].explanation_template.format(**params)


def sample_params(code: ExceptionCode) -> dict[str, str]:
    return dict(SPECS[code].sample_params)
