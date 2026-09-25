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
            "The file couldn't be read clearly ({reason}).",
            "Ask the supplier for a clearer copy",
            {"reason": "page 2 is blank or too blurry"},
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
            "{basis} {expected}, but the invoice {field} is {actual}.",
            "Ask the supplier for a corrected invoice",
            {
                "basis": "The lines add up to",
                "expected": "USD 2,140.00",
                "field": "total",
                "actual": "USD 2,410.00",
            },
        ),
        _spec(
            _C.TAX_MISMATCH,
            _S.REVIEW,
            "Tax is {actual}, but {basis} is {expected}.",
            "Check the tax rate for this supplier",
            {
                "actual": "USD 318.00",
                "basis": "15% of USD 2,140.00",
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
            "'{name}' doesn't match any known supplier.{closest_note}",
            "Link to an existing supplier or create a new one",
            {
                "name": "Acme Supplies Ltd",
                "closest_note": " Closest match: 'ACME Supply Co.' (82%).",
            },
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
            "This looks like invoice {other} from the same supplier: {match}, "
            "with invoice dates {days} days apart.",
            "Compare them side by side; reject if duplicate",
            {
                "other": "INV-1043",
                "match": "the same amount and a very similar number",
                "days": "4",
            },
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
            "{po} {problem}.",
            "Check for a typo or ask purchasing",
            {"po": "PO-7781", "problem": "isn't in the system"},
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
            "Line {line} bills {actual} units{earlier}, but the PO line is for {expected}.",
            "Ask the supplier or purchasing",
            {"line": "1", "actual": "120", "earlier": "", "expected": "100"},
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
            "Line {line} bills {billed} units{earlier}, but only {received} have been received.",
            "Hold, or pay partially once the rest arrives",
            {"line": "1", "billed": "100", "earlier": "", "received": "80"},
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
            "The invoice is in {invoice_currency}, but {against} {expected_currency}.",
            "Confirm the currency with the supplier",
            {"invoice_currency": "EUR", "against": "the PO is in", "expected_currency": "USD"},
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


@dataclass(frozen=True)
class Variant:
    """A second wording for a code when the usual sentence does not fit the facts."""

    template: str
    sample_params: dict[str, str]


VARIANTS: dict[tuple[ExceptionCode, str], Variant] = {
    (ExceptionCode.QTY_VARIANCE, "line_not_on_po"): Variant(
        "Line {line} could not be matched to any line on the PO.", {"line": "3"}
    ),
}

# What each check looks at, for "could not be checked" (a check that was skipped is never a pass).
CHECK_SUBJECTS: dict[ExceptionCode, str] = {
    _C.UNREADABLE_DOCUMENT: "the document",
    _C.LOW_CONFIDENCE_FIELD: "the field confidence",
    _C.LINE_MATH_MISMATCH: "the line arithmetic",
    _C.TOTAL_MISMATCH: "the totals",
    _C.TAX_MISMATCH: "the tax",
    _C.INVALID_DATE: "the dates",
    _C.UNKNOWN_SUPPLIER: "the supplier",
    _C.BANK_DETAILS_CHANGED: "the bank account",
    _C.POSSIBLE_DUPLICATE: "the duplicate check",
    _C.NO_PO: "the purchase order lookup",
    _C.PO_NOT_FOUND: "the purchase order lookup",
    _C.PRICE_VARIANCE: "the price comparison with the PO",
    _C.QTY_VARIANCE: "the quantity comparison with the PO",
    _C.RECEIPT_MISSING: "the goods receipt check",
    _C.QTY_NOT_RECEIVED: "the received quantity comparison",
    _C.PO_OVERBILLED: "the PO billing total",
    _C.CURRENCY_MISMATCH: "the currency",
    _C.ABOVE_APPROVAL_LIMIT: "the approval limit",
}

UNCHECKED_TEMPLATE = "{subject} could not be checked ({reason}), so a person needs to look at it."


def explain(code: ExceptionCode, variant: str | None = None, **params: str) -> str:
    """Render the explanation for a code. A missing parameter or unknown variant raises KeyError."""
    template = (
        SPECS[code].explanation_template if variant is None else VARIANTS[(code, variant)].template
    )
    return template.format(**params)


def explain_unchecked(code: ExceptionCode, reason: str) -> str:
    """The sentence for a check that could not be done. `reason` is plain words, not a code."""
    subject = CHECK_SUBJECTS[code]
    return UNCHECKED_TEMPLATE.format(subject=subject[0].upper() + subject[1:], reason=reason)


def sample_params(code: ExceptionCode) -> dict[str, str]:
    return dict(SPECS[code].sample_params)


def taxonomy_markdown() -> str:
    """The table in docs/exception-taxonomy.md. A test keeps the doc in sync with SPECS."""
    rows = [
        "| Code | Severity | Example explanation | Suggested fix |",
        "|---|---|---|---|",
    ]
    for code in ExceptionCode:
        spec = SPECS[code]
        example = explain(code, **spec.sample_params)
        cells = [f"`{code.value}`", spec.severity.value, example, spec.suggested_fix]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)
