"""3-way matching: invoice, purchase order and goods receipts (playbook §6.6). Pure.

The caller passes the invoice and the purchase orders it might belong to, each carrying what has
already been received and billed against it. Every check code always produces a result (pass, fail
or skipped) so the audit trail shows what was compared. A check that cannot be done is `skipped`
with a reason, never a pass: uncertain means human.

Money is integer minor units. Quantities are Decimal. Tolerances are basis points (1% = 100).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from rapidfuzz import fuzz

from intake.core.dedupe import number_key
from intake.core.validate import Outcome
from intake.core.validate import _int_setting as int_setting

RULE_VERSION = "v1"
_BP = 10_000
_TIE_MARGIN = 5  # two PO lines this close in description score cannot be told apart


class MatchCode(StrEnum):
    """Each check is named after the exception it raises when it fails (M7 creates them)."""

    NO_PO = "NO_PO"
    PO_NOT_FOUND = "PO_NOT_FOUND"
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QTY_VARIANCE = "QTY_VARIANCE"
    RECEIPT_MISSING = "RECEIPT_MISSING"
    QTY_NOT_RECEIVED = "QTY_NOT_RECEIVED"
    PO_OVERBILLED = "PO_OVERBILLED"


@dataclass(frozen=True)
class MatchSettings:
    price_tolerance_bp: int = 200  # unit price may differ from the PO by this much, either way
    qty_tolerance_bp: int = 0  # billed quantity may exceed the PO quantity by this much
    description_similarity_min: int = 80  # 0-100, for matching lines that have no SKU
    po_total_tolerance_bp: int = 200  # for inferring a PO when the invoice names none
    overbill_tolerance_minor: int = 0

    @classmethod
    def from_tenant(cls, raw: Mapping[str, object]) -> "MatchSettings":
        d = cls()
        return cls(
            price_tolerance_bp=int_setting(
                raw, "match_price_tolerance_bp", d.price_tolerance_bp, 0, _BP
            ),
            qty_tolerance_bp=int_setting(
                raw, "match_qty_tolerance_bp", d.qty_tolerance_bp, 0, 100 * _BP
            ),
            description_similarity_min=int_setting(
                raw, "match_description_similarity_min", d.description_similarity_min, 0, 100
            ),
            po_total_tolerance_bp=int_setting(
                raw, "match_po_total_tolerance_bp", d.po_total_tolerance_bp, 0, _BP
            ),
            overbill_tolerance_minor=int_setting(
                raw, "match_overbill_tolerance_minor", d.overbill_tolerance_minor, 0, 10**9
            ),
        )


@dataclass(frozen=True)
class InvoiceLineFacts:
    line_no: int
    sku: str | None
    description: str | None
    qty: Decimal | None
    unit_price_minor: int | None
    amount_minor: int | None


@dataclass(frozen=True)
class MatchInvoice:
    id: str
    supplier_id: str | None
    po_number: str | None
    currency: str | None
    subtotal_minor: int | None
    lines: tuple[InvoiceLineFacts, ...]


@dataclass(frozen=True)
class PoLineRef:
    id: str
    line_no: int
    sku: str | None
    description: str
    qty: Decimal
    unit_price_minor: int
    qty_received: Decimal  # all receipts for the PO, summed
    qty_billed_before: Decimal  # matched to this line on earlier invoices


@dataclass(frozen=True)
class PoRef:
    id: str
    po_number: str
    supplier_id: str
    currency: str
    total_minor: int
    status: str = "open"
    lines: tuple[PoLineRef, ...] = ()
    receipt_count: int = 0
    billed_before_minor: int = 0  # subtotals of earlier invoices matched to this PO


@dataclass(frozen=True)
class LineMatch:
    invoice_line_no: int
    po_line_id: str
    method: str  # "sku", "description" or "amount"


@dataclass(frozen=True)
class MatchCheck:
    code: MatchCode
    outcome: Outcome
    details: dict[str, object]


@dataclass(frozen=True)
class MatchResult:
    checks: tuple[MatchCheck, ...]
    po_id: str | None
    inferred: bool
    line_matches: tuple[LineMatch, ...]
    version: str = RULE_VERSION
    # What this invoice bills against its PO, for later invoices to count. None when it is unknown
    # or must not be counted (no PO, another currency, a credit note).
    billed_minor: int | None = None

    @property
    def passed(self) -> bool:
        return all(c.outcome is Outcome.PASS for c in self.checks)


def normalize_po_number(number: str | None) -> str:
    return number_key(number or "")


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _text_key(text: str | None) -> str:
    return re.sub(r"[\W_]+", " ", (text or "").casefold()).strip()


# ---- finding the PO -------------------------------------------------------------------------


@dataclass(frozen=True)
class _Found:
    po: PoRef | None
    inferred: bool
    no_po: MatchCheck
    not_found: MatchCheck


def _found(po: PoRef | None, no_po: MatchCheck, not_found: MatchCheck, inferred: bool) -> _Found:
    return _Found(po, inferred, no_po, not_found)


def _find_po(invoice: MatchInvoice, pos: Sequence[PoRef], settings: MatchSettings) -> _Found:
    ok_no_po = MatchCheck(MatchCode.NO_PO, Outcome.PASS, {})
    ok_not_found = MatchCheck(MatchCode.PO_NOT_FOUND, Outcome.PASS, {})
    key = normalize_po_number(invoice.po_number)
    if key:
        named = [p for p in pos if normalize_po_number(p.po_number) == key]
        mine = [p for p in named if p.supplier_id == invoice.supplier_id]
        if named and not invoice.supplier_id:  # whose PO it is cannot be told: a person decides
            unknown: dict[str, object] = {
                "po_number": invoice.po_number,
                "reason": "SUPPLIER_UNKNOWN",
            }
            skipped = MatchCheck(MatchCode.PO_NOT_FOUND, Outcome.SKIPPED, unknown)
            return _found(None, ok_no_po, skipped, False)
        if len(mine) > 1:  # punctuation is ignored, so "PO-12" and "PO12" can collide
            both: dict[str, object] = {
                "reason": "AMBIGUOUS_PO",
                "candidates": sorted(p.po_number for p in mine),
            }
            ambiguous = MatchCheck(MatchCode.NO_PO, Outcome.SKIPPED, both)
            return _found(None, ambiguous, ok_not_found, False)
        if mine and mine[0].status == "open":
            return _found(mine[0], ok_no_po, ok_not_found, False)
        reason = "PO_NOT_OPEN" if mine else "PO_OF_OTHER_SUPPLIER" if named else "NOT_IN_SYSTEM"
        detail: dict[str, object] = {"po_number": invoice.po_number, "reason": reason}
        if mine:
            detail["status"] = mine[0].status
        return _found(
            None, ok_no_po, MatchCheck(MatchCode.PO_NOT_FOUND, Outcome.FAIL, detail), False
        )

    if not invoice.supplier_id or invoice.subtotal_minor is None or not invoice.currency:
        skipped = MatchCheck(MatchCode.NO_PO, Outcome.SKIPPED, {"reason": "CANNOT_INFER_PO"})
        return _found(None, skipped, ok_not_found, False)
    subtotal = invoice.subtotal_minor
    candidates = [
        p
        for p in pos
        if p.supplier_id == invoice.supplier_id
        and p.status == "open"
        and p.currency == invoice.currency
        and abs(p.total_minor - subtotal) * _BP <= p.total_minor * settings.po_total_tolerance_bp
    ]
    if len(candidates) == 1:
        only = candidates[0]
        # matched, but never clean: a PO guessed from the total alone is for a person to confirm
        details: dict[str, object] = {"reason": "PO_INFERRED", "inferred_po_number": only.po_number}
        return _found(
            only, MatchCheck(MatchCode.NO_PO, Outcome.SKIPPED, details), ok_not_found, True
        )
    if candidates:
        ambiguous = MatchCheck(
            MatchCode.NO_PO,
            Outcome.SKIPPED,
            {"reason": "AMBIGUOUS_PO", "candidates": sorted(p.po_number for p in candidates)},
        )
        return _found(None, ambiguous, ok_not_found, False)
    none = {"reason": "NO_OPEN_PO_WITH_MATCHING_TOTAL", "invoice_subtotal_minor": subtotal}
    return _found(None, MatchCheck(MatchCode.NO_PO, Outcome.FAIL, none), ok_not_found, False)


# ---- matching lines -------------------------------------------------------------------------


def _sku_conflict(line: InvoiceLineFacts, po_line: PoLineRef) -> bool:
    """Both sides name a SKU and the SKUs differ: a substitution, not a rewording."""
    a, b = number_key(line.sku or ""), number_key(po_line.sku or "")
    return bool(a) and bool(b) and a != b


def match_lines(
    invoice_lines: Sequence[InvoiceLineFacts],
    po_lines: Sequence[PoLineRef],
    settings: MatchSettings,
) -> tuple[LineMatch, ...]:
    """SKU first, then description similarity, then amount. A PO line is used at most once and a
    line that could be either of two PO lines is left unmatched rather than guessed."""
    used: set[str] = set()
    found: dict[int, LineMatch] = {}

    def take(line: InvoiceLineFacts, po_line: PoLineRef, method: str) -> None:
        used.add(po_line.id)
        found[line.line_no] = LineMatch(line.line_no, po_line.id, method)

    for line in invoice_lines:
        sku = number_key(line.sku or "")
        if not sku:
            continue
        same = [p for p in po_lines if p.id not in used and number_key(p.sku or "") == sku]
        if len(same) == 1:  # a SKU on several PO lines says nothing about which one
            take(line, same[0], "sku")

    for line in invoice_lines:
        text = _text_key(line.description)
        if line.line_no in found or not text:
            continue
        scored = sorted(
            (
                (fuzz.token_sort_ratio(text, _text_key(p.description)), p)
                for p in po_lines
                if p.id not in used and not _sku_conflict(line, p)
            ),
            key=lambda sp: -sp[0],
        )
        if not scored or scored[0][0] < settings.description_similarity_min:
            continue
        if len(scored) > 1 and scored[0][0] - scored[1][0] < _TIE_MARGIN:
            continue
        take(line, scored[0][1], "description")

    for line in invoice_lines:
        if line.line_no in found or line.amount_minor is None:
            continue
        same_amount = [
            p
            for p in po_lines
            if p.id not in used
            and not _sku_conflict(line, p)
            and p.qty * p.unit_price_minor == line.amount_minor
        ]
        if len(same_amount) == 1:
            take(line, same_amount[0], "amount")

    return tuple(found[n] for n in sorted(found))


# ---- the checks -----------------------------------------------------------------------------


def _skip(code: MatchCode, reason: str) -> MatchCheck:
    return MatchCheck(code, Outcome.SKIPPED, {"reason": reason})


def _verdict(
    code: MatchCode, findings: list[dict[str, object]], unreadable: bool, matched: int
) -> MatchCheck:
    if findings:
        return MatchCheck(code, Outcome.FAIL, {"findings": findings})
    if unreadable:
        return _skip(code, "UNREADABLE_LINE")
    if not matched:
        return _skip(code, "NO_MATCHED_LINES")
    return MatchCheck(code, Outcome.PASS, {})


def _invoice_subtotal(invoice: MatchInvoice) -> int | None:
    if invoice.subtotal_minor is not None:
        return invoice.subtotal_minor
    amounts = [ln.amount_minor for ln in invoice.lines]
    if amounts and all(a is not None for a in amounts):
        return sum(a for a in amounts if a is not None)
    return None


def _sanitized(invoice: MatchInvoice) -> MatchInvoice:
    """A quantity that is not a finite number (NaN, infinity) is unreadable, not a value."""
    if all(ln.qty is None or ln.qty.is_finite() for ln in invoice.lines):
        return invoice
    lines = tuple(
        replace(ln, qty=None) if ln.qty is not None and not ln.qty.is_finite() else ln
        for ln in invoice.lines
    )
    return replace(invoice, lines=lines)


def _is_credit(invoice: MatchInvoice) -> bool:
    return (invoice.subtotal_minor is not None and invoice.subtotal_minor < 0) or any(
        (ln.qty is not None and ln.qty < 0) or (ln.amount_minor is not None and ln.amount_minor < 0)
        for ln in invoice.lines
    )


def check_match(
    invoice: MatchInvoice, pos: Sequence[PoRef], settings: MatchSettings
) -> MatchResult:
    """Compare `invoice` with its PO and the receipts and earlier billing recorded on that PO."""
    invoice = _sanitized(invoice)
    found = _find_po(invoice, pos, settings)
    po = found.po
    head = (found.no_po, found.not_found)
    if po is None:
        rest = tuple(_skip(c, "NO_PO") for c in _AFTER_PO)
        return MatchResult(head + rest, None, False, ())
    if _is_credit(invoice):  # negative amounts would lower what is counted as billed
        rest = tuple(_skip(c, "CREDIT_NOTE") for c in _AFTER_PO)
        return MatchResult(head + rest, po.id, found.inferred, ())

    lines_reason = "NO_LINES" if not invoice.lines else "PO_HAS_NO_LINES" if not po.lines else None
    matches = match_lines(invoice.lines, po.lines, settings) if lines_reason is None else ()
    by_line = {ln.line_no: ln for ln in invoice.lines}
    po_by_id = {p.id: p for p in po.lines}
    pairs = [(by_line[m.invoice_line_no], po_by_id[m.po_line_id]) for m in matches]

    price_findings: list[dict[str, object]] = []
    qty_findings: list[dict[str, object]] = []
    recv_findings: list[dict[str, object]] = []
    price_unreadable = qty_unreadable = False

    for ln in invoice.lines if lines_reason is None else ():
        if ln.line_no not in {m.invoice_line_no for m in matches}:
            qty_findings.append({"line": ln.line_no, "kind": "LINE_NOT_ON_PO"})

    for ln, pl in pairs:
        if ln.unit_price_minor is None:
            price_unreadable = True
        else:
            diff = abs(ln.unit_price_minor - pl.unit_price_minor)
            if diff * _BP > pl.unit_price_minor * settings.price_tolerance_bp:
                price_findings.append(
                    {
                        "line": ln.line_no,
                        "invoice_unit_price_minor": ln.unit_price_minor,
                        "po_unit_price_minor": pl.unit_price_minor,
                        "variance_bp": diff * _BP // pl.unit_price_minor
                        if pl.unit_price_minor
                        else None,
                        "limit_bp": settings.price_tolerance_bp,
                    }
                )
        if ln.qty is None:
            qty_unreadable = True
            continue
        billed = pl.qty_billed_before + ln.qty
        if billed * _BP > pl.qty * (_BP + settings.qty_tolerance_bp):
            qty_findings.append(
                {
                    "line": ln.line_no,
                    "kind": "OVER_PO_QTY",
                    "po_qty": _fmt(pl.qty),
                    "billed_qty": _fmt(ln.qty),
                    "billed_before_qty": _fmt(pl.qty_billed_before),
                }
            )
        if billed > pl.qty_received:
            recv_findings.append(
                {
                    "line": ln.line_no,
                    "billed_qty": _fmt(ln.qty),
                    "received_qty": _fmt(pl.qty_received),
                    "billed_before_qty": _fmt(pl.qty_billed_before),
                }
            )

    currency_reason = (
        None
        if invoice.currency == po.currency
        else "CURRENCY_DIFFERS_FROM_PO"
        if invoice.currency
        else "CURRENCY_UNKNOWN"
    )
    if lines_reason:
        price = _skip(MatchCode.PRICE_VARIANCE, lines_reason)
        qty = _skip(MatchCode.QTY_VARIANCE, lines_reason)
    else:
        price = (
            _skip(MatchCode.PRICE_VARIANCE, currency_reason)
            if currency_reason
            else _verdict(MatchCode.PRICE_VARIANCE, price_findings, price_unreadable, len(pairs))
        )
        qty = _verdict(MatchCode.QTY_VARIANCE, qty_findings, qty_unreadable, len(pairs))

    if po.receipt_count == 0:
        receipt = MatchCheck(MatchCode.RECEIPT_MISSING, Outcome.FAIL, {"po_number": po.po_number})
        not_received = _skip(MatchCode.QTY_NOT_RECEIVED, lines_reason or "NO_RECEIPT")
    else:
        receipt = MatchCheck(MatchCode.RECEIPT_MISSING, Outcome.PASS, {})
        not_received = (
            _skip(MatchCode.QTY_NOT_RECEIVED, lines_reason)
            if lines_reason
            else _verdict(MatchCode.QTY_NOT_RECEIVED, recv_findings, qty_unreadable, len(pairs))
        )

    subtotal = _invoice_subtotal(invoice)
    if currency_reason:
        overbilled = _skip(MatchCode.PO_OVERBILLED, currency_reason)
    elif subtotal is None:
        overbilled = _skip(MatchCode.PO_OVERBILLED, "NO_SUBTOTAL")
    else:
        total = po.billed_before_minor + subtotal
        over = total - po.total_minor
        info: dict[str, object] = {
            "po_number": po.po_number,
            "po_total_minor": po.total_minor,
            "billed_before_minor": po.billed_before_minor,
            "invoice_subtotal_minor": subtotal,
            "billed_total_minor": total,
            "over_minor": over,
            "currency": po.currency,
        }
        failed = over > settings.overbill_tolerance_minor
        overbilled = MatchCheck(
            MatchCode.PO_OVERBILLED,
            Outcome.FAIL if failed else Outcome.PASS,
            info if failed else {},
        )

    checks = head + (price, qty, receipt, not_received, overbilled)
    billed_now = None if currency_reason else subtotal
    return MatchResult(checks, po.id, found.inferred, matches, billed_minor=billed_now)


_AFTER_PO = (
    MatchCode.PRICE_VARIANCE,
    MatchCode.QTY_VARIANCE,
    MatchCode.RECEIPT_MISSING,
    MatchCode.QTY_NOT_RECEIVED,
    MatchCode.PO_OVERBILLED,
)
