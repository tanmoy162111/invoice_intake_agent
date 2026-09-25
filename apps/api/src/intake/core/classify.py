"""Turn check results into exceptions with plain-language explanations (playbook §6.7). Pure.

Every failed check becomes one exception (one row per check; its explanation lists every affected
line with the real numbers). Every skipped check ("could not be checked") becomes one too, under the
same code, at review severity, because a check that could not be done is never a pass. Two checks
that are not stored as check results are raised here: doubtful critical fields and a total above
the approval limit. Explanations come from the templates in `core/exceptions.py`, never free text.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from intake.core.exceptions import (
    SPECS,
    ExceptionCode,
    Severity,
    explain,
    explain_unchecked,
)
from intake.core.money import CURRENCY_EXPONENT, Money, format_money
from intake.core.validate import Outcome

_C = ExceptionCode

REASON_TEXT: dict[str, str] = {
    "MISSING_AMOUNTS": "the subtotal or the tax amount was not read",
    "NO_RATE_TO_COMPARE": "there is no tax rate on file for this supplier or on the lines",
    "NO_INVOICE_DATE": "the invoice date was not read",
    "UNKNOWN_SUPPLIER": "the supplier is not known",
    "NOTHING_TO_MATCH": "neither a supplier name nor a tax ID was read",
    "BANK_NOT_READ": "the bank account on the invoice could not be read",
    "NO_BANK_ON_FILE": "there is no bank account on file for this supplier to compare with",
    "NO_SUPPLIER": "the supplier is not known",
    "NO_INVOICE_NUMBER": "the invoice number was not read",
    "INCOMPLETE_FOR_SOFT_MATCH": (
        "a similar earlier invoice, or this one, is missing its total, date or currency"
    ),
    "SUPPLIER_UNCERTAIN": (
        "a similar earlier invoice has a supplier that could be the same company "
        "written another way"
    ),
    "EARLIER_INVOICES_STILL_PENDING": "earlier invoices were still being read when the wait ended",
    "TOO_MANY_TO_COMPARE": "there are too many earlier invoices to compare",
    "NO_PO": "no purchase order was found",
    "CANNOT_INFER_PO": "no PO number was printed and the supplier, currency or subtotal is missing",
    "PO_INFERRED": "no PO number was printed, so the PO was guessed from the amount",
    "SUPPLIER_UNKNOWN": "the PO number was found but the supplier of this invoice is not known",
    "AMBIGUOUS_PO": "more than one purchase order fits",
    "CREDIT_NOTE": "the invoice has a negative amount or quantity (a credit note)",
    "NO_LINES": "no invoice lines were read",
    "PO_HAS_NO_LINES": "the purchase order has no lines",
    "NO_RECEIPT": "nothing has been received yet",
    "NO_MATCHED_LINES": "no invoice line could be matched to a PO line",
    "UNREADABLE_LINE": "a line's price or quantity could not be read",
    "CURRENCY_DIFFERS_FROM_PO": "the invoice currency differs from the PO currency",
    "CURRENCY_UNKNOWN": "the invoice currency was not read",
    "NO_SUBTOTAL": "the subtotal could not be read",
    "EARLIER_BILLING_UNKNOWN": "an earlier invoice on the same PO has an unreadable amount",
    "TOO_MANY_POS_TO_COMPARE": "there are too many purchase orders to search",
}  # fmt: skip

# A skipped check whose reason is just the consequence of another raised exception is not repeated.
_DERIVED_FROM: dict[str, tuple[ExceptionCode, ...]] = {
    "UNKNOWN_SUPPLIER": (_C.UNKNOWN_SUPPLIER,),
    "NO_PO": (_C.NO_PO, _C.PO_NOT_FOUND),
    "NO_RECEIPT": (_C.RECEIPT_MISSING,),
}


@dataclass(frozen=True)
class CheckRow:
    code: str
    outcome: Outcome
    details: Mapping[str, object]


@dataclass(frozen=True)
class WeakField:
    field: str
    confidence: Decimal | None  # None: the field was not read at all
    minimum: Decimal


@dataclass(frozen=True)
class ClassifyContext:
    currency: str | None
    total_minor: int | None
    supplier_name: str | None  # the name on file for the linked supplier, if any
    approval_limit_minor: int | None
    weak_fields: Sequence[WeakField]


@dataclass(frozen=True)
class ExceptionDraft:
    code: ExceptionCode
    severity: Severity
    explanation: str
    suggested_fix: str
    unchecked: bool  # True: the check could not be done (it did not find a problem)
    evidence: dict[str, Any] = field(default_factory=dict)


# ---- formatting -------------------------------------------------------------------------------


def _money(minor: object, currency: str | None) -> str:
    amount = int(minor)  # type: ignore[call-overload]
    if currency in CURRENCY_EXPONENT:
        return format_money(Money(amount, str(currency)))
    return f"{Decimal(amount) / 100:,.2f}"


def _bp(bp: object) -> str:
    return format((Decimal(int(bp)) / 100).normalize(), "f") + "%"  # type: ignore[call-overload]


def _signed_percent(actual: int, expected: int) -> str:
    pct = (Decimal(actual - expected) / Decimal(expected) * 100).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return f"{'+' if pct > 0 else ''}{pct}%"


def _earlier(before: object) -> str:
    if Decimal(str(before)) > 0:
        return f" (on top of {before} already billed on earlier invoices)"
    return ""


def _findings(d: Mapping[str, object]) -> list[Mapping[str, Any]]:
    found = d["findings"]
    assert isinstance(found, list)
    return found


# ---- one builder per code: the failed check's details in, the sentences out --------------------


def _line_math(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    lines: Any = d["lines"]
    return [
        explain(
            _C.LINE_MATH_MISMATCH,
            line=str(ln["line_no"]), qty=str(ln["quantity"]),
            unit_price=_money(ln["unit_price_minor"], c),
            expected=_money(ln["expected_minor"], c), actual=_money(ln["amount_minor"], c),
        )
        for ln in lines
    ]  # fmt: skip


def _totals(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    basis = {
        "LINES_SUM_TO_SUBTOTAL": ("The lines add up to", "subtotal"),
        "SUBTOTAL_PLUS_TAX_IS_TOTAL": ("The subtotal plus tax comes to", "total"),
    }
    checks: Any = d["checks"]
    return [
        explain(
            _C.TOTAL_MISMATCH, basis=basis[k["name"]][0], expected=_money(k["expected_minor"], c),
            field=basis[k["name"]][1], actual=_money(k["actual_minor"], c),
        )
        for k in checks
        if not k["ok"]
    ]  # fmt: skip


def _tax(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    if d["source"] == "supplier":
        basis = f"{_bp(d['rate_bp'])} of {_money(d['subtotal_minor'], c)}"
    else:
        basis = "the tax on each line"
    return [
        explain(
            _C.TAX_MISMATCH, actual=_money(d["actual_minor"], c), basis=basis,
            expected=_money(d["expected_minor"], c),
        )
    ]  # fmt: skip


def _dates(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    out: list[str] = []
    reasons: Any = d["reasons"]
    for r in reasons:
        if r["code"] == "FUTURE_INVOICE_DATE":
            field_, why = (
                "invoice date",
                f"it is in the future ({r['invoice_date']}; today is {d['today']})",
            )
        elif r["code"] == "INVOICE_TOO_OLD":
            field_, why = (
                "invoice date",
                f"it is more than {r['max_age_days']} days old ({r['invoice_date']})",
            )
        else:
            field_, why = (
                "due date",
                (
                    f"it is before the invoice date "
                    f"(due {r['due_date']}, invoice dated {r['invoice_date']})"
                ),
            )
        out.append(explain(_C.INVALID_DATE, field=field_, reason=why))
    return out


def _currency(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    printed = d.get("printed")
    seen = d.get("invoice_currency") or printed or "an unknown currency"
    return [
        explain(
            _C.CURRENCY_MISMATCH, invoice_currency=str(seen),
            against="this supplier usually invoices in",
            expected_currency=str(d["supplier_currency"]),
        )
    ]  # fmt: skip


def _unknown_supplier(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    name = d.get("printed_name") or (
        f"Tax ID {d['printed_tax_id']}" if d.get("printed_tax_id") else "(no name printed)"
    )
    note = ""
    if d.get("closest_name") and d.get("closest_score"):
        note = f" Closest match: '{d['closest_name']}' ({d['closest_score']}%)."
    return [explain(_C.UNKNOWN_SUPPLIER, name=str(name), closest_note=note)]


def _bank(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    return [explain(_C.BANK_DETAILS_CHANGED, supplier=x.supplier_name or "this supplier")]


def _duplicate(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    match = (
        "the same invoice number"
        if d["kind"] == "hard"
        else "the same amount and a very similar number"
    )
    days = d.get("days_apart")
    return [
        explain(
            _C.POSSIBLE_DUPLICATE,
            other=str(d.get("existing_invoice_number") or "an earlier invoice"),
            match=match, days="an unknown number of" if days is None else str(days),
        )
    ]  # fmt: skip


def _no_po(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    return [explain(_C.NO_PO)]


_PO_PROBLEM = {
    "NOT_IN_SYSTEM": "isn't in the system",
    "PO_OF_OTHER_SUPPLIER": "belongs to a different supplier",
}


def _po_not_found(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    reason = d.get("reason")
    if reason == "PO_NOT_OPEN":
        problem = f"is not open (its status is {d['status']})"
    else:
        problem = _PO_PROBLEM[str(reason)]
    return [explain(_C.PO_NOT_FOUND, po=str(d["po_number"]), problem=problem)]


def _price(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    out = []
    for f in _findings(d):
        actual, expected = int(f["invoice_unit_price_minor"]), int(f["po_unit_price_minor"])
        variance = (
            _signed_percent(actual, expected) if expected else "no percentage, the PO price is zero"
        )
        out.append(
            explain(
                _C.PRICE_VARIANCE, line=str(f["line"]), actual=_money(actual, c),
                expected=_money(expected, c), variance=variance, limit=_bp(f["limit_bp"]),
            )
        )  # fmt: skip
    return out


def _qty(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    out = []
    for f in _findings(d):
        if f["kind"] == "LINE_NOT_ON_PO":
            out.append(explain(_C.QTY_VARIANCE, variant="line_not_on_po", line=str(f["line"])))
        else:
            out.append(
                explain(
                    _C.QTY_VARIANCE, line=str(f["line"]), actual=str(f["billed_qty"]),
                    earlier=_earlier(f["billed_before_qty"]), expected=str(f["po_qty"]),
                )
            )  # fmt: skip
    return out


def _receipt(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    return [explain(_C.RECEIPT_MISSING, po=str(d["po_number"]))]


def _not_received(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    return [
        explain(
            _C.QTY_NOT_RECEIVED, line=str(f["line"]), billed=str(f["billed_qty"]),
            earlier=_earlier(f["billed_before_qty"]), received=str(f["received_qty"]),
        )
        for f in _findings(d)
    ]  # fmt: skip


def _overbilled(d: Mapping[str, object], c: str | None, x: ClassifyContext) -> list[str]:
    cur = str(d["currency"])
    return [
        explain(
            _C.PO_OVERBILLED, po=str(d["po_number"]), billed=_money(d["billed_total_minor"], cur),
            total=_money(d["po_total_minor"], cur),
        )
    ]  # fmt: skip


_Builder = Callable[[Mapping[str, object], str | None, ClassifyContext], list[str]]
_BUILDERS: dict[ExceptionCode, _Builder] = {
    _C.LINE_MATH_MISMATCH: _line_math,
    _C.TOTAL_MISMATCH: _totals,
    _C.TAX_MISMATCH: _tax,
    _C.INVALID_DATE: _dates,
    _C.CURRENCY_MISMATCH: _currency,
    _C.UNKNOWN_SUPPLIER: _unknown_supplier,
    _C.BANK_DETAILS_CHANGED: _bank,
    _C.POSSIBLE_DUPLICATE: _duplicate,
    _C.NO_PO: _no_po,
    _C.PO_NOT_FOUND: _po_not_found,
    _C.PRICE_VARIANCE: _price,
    _C.QTY_VARIANCE: _qty,
    _C.RECEIPT_MISSING: _receipt,
    _C.QTY_NOT_RECEIVED: _not_received,
    _C.PO_OVERBILLED: _overbilled,
}


# ---- putting it together ----------------------------------------------------------------------


def _draft(
    code: ExceptionCode, text: str, unchecked: bool, evidence: Mapping[str, object]
) -> ExceptionDraft:
    spec = SPECS[code]
    return ExceptionDraft(
        code=code,
        severity=Severity.REVIEW if unchecked else spec.severity,
        explanation=text,
        suggested_fix=spec.suggested_fix,
        unchecked=unchecked,
        evidence=dict(evidence),
    )


def _failed(row: CheckRow, code: ExceptionCode, x: ClassifyContext) -> ExceptionDraft:
    try:
        sentences = _BUILDERS[code](row.details, x.currency, x)
    except (KeyError, TypeError, ValueError, AssertionError, ArithmeticError):
        sentences = []
    if not sentences:  # the check failed, but its details are unusable: still shown, never dropped
        text = explain_unchecked(code, "the details of the failure were not recorded")
        return ExceptionDraft(
            code, SPECS[code].severity, text, SPECS[code].suggested_fix, False, dict(row.details)
        )
    return _draft(code, " ".join(sentences), False, row.details)


def _skipped(row: CheckRow, code: ExceptionCode) -> ExceptionDraft:
    reason = row.details.get("reason")
    words = REASON_TEXT.get(str(reason)) if reason else "no reason was recorded"
    return _draft(
        code, explain_unchecked(code, words or f"unrecognised reason {reason}"), True, row.details
    )


def _field_name(name: str) -> str:
    return name.replace("_", " ")


def _weak_field_draft(weak: Sequence[WeakField]) -> ExceptionDraft:
    parts = []
    for w in weak:
        if w.confidence is None:
            why = "it could not be read"
        else:
            have = (w.confidence * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            need = (w.minimum * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            why = f"its confidence is {have}%, below the {need}% minimum"
        parts.append(explain(_C.LOW_CONFIDENCE_FIELD, field=_field_name(w.field), reason=why))
    evidence = {w.field: None if w.confidence is None else str(w.confidence) for w in weak}
    return _draft(_C.LOW_CONFIDENCE_FIELD, " ".join(parts), False, evidence)


def classify(checks: Sequence[CheckRow], ctx: ClassifyContext) -> list[ExceptionDraft]:
    """Exceptions for the invoice: blocks first, then in taxonomy order. Raises ValueError for a
    check code that is not in the taxonomy (a stored result must never be silently ignored)."""
    drafts: list[ExceptionDraft] = []
    skipped: list[tuple[CheckRow, ExceptionCode]] = []
    for row in checks:
        code = ExceptionCode(row.code)
        if row.outcome is Outcome.FAIL:
            drafts.append(_failed(row, code, ctx))
        elif row.outcome is Outcome.SKIPPED:
            skipped.append((row, code))
    raised = {d.code for d in drafts} | {c for _, c in skipped if c is _C.UNKNOWN_SUPPLIER}
    raised |= {c for r, c in skipped if str(r.details.get("reason")) not in _DERIVED_FROM}
    credit_seen = False
    for row, code in skipped:
        reason = str(row.details.get("reason"))
        if reason in _DERIVED_FROM and raised & set(_DERIVED_FROM[reason]):
            continue  # the root problem is already an exception of its own
        if reason == "CREDIT_NOTE":
            if credit_seen:
                continue
            credit_seen = True
        drafts.append(_skipped(row, code))
    if ctx.weak_fields:
        drafts.append(_weak_field_draft(ctx.weak_fields))
    if (
        ctx.total_minor is not None
        and ctx.approval_limit_minor is not None
        and ctx.total_minor > ctx.approval_limit_minor
    ):
        text = explain(
            _C.ABOVE_APPROVAL_LIMIT, total=_money(ctx.total_minor, ctx.currency),
            limit=_money(ctx.approval_limit_minor, ctx.currency),
        )  # fmt: skip
        drafts.append(
            _draft(
                _C.ABOVE_APPROVAL_LIMIT, text, False,
                {"total_minor": ctx.total_minor, "limit_minor": ctx.approval_limit_minor},
            )
        )  # fmt: skip
    order = list(ExceptionCode)
    return sorted(drafts, key=lambda d: (d.severity is not Severity.BLOCK, order.index(d.code)))
