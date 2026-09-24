"""Deterministic validation rules (playbook §6.4). Pure: facts in, check results out.

Every check always produces a result with one of three outcomes. `skipped` means the check could
not be done (a value was missing or unreadable): it is never a pass, because uncertain means human.
The details carry the real numbers a later explanation needs. Bank hashes are never put in details.
"""

import hmac
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from rapidfuzz import fuzz

from intake.core.money import line_amount_minor

RULE_VERSION = "v1"
_SYMBOL_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}


class CheckCode(StrEnum):
    """Each check is named after the exception it raises when it fails (M7 creates them)."""

    LINE_MATH_MISMATCH = "LINE_MATH_MISMATCH"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    TAX_MISMATCH = "TAX_MISMATCH"
    INVALID_DATE = "INVALID_DATE"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    UNKNOWN_SUPPLIER = "UNKNOWN_SUPPLIER"
    BANK_DETAILS_CHANGED = "BANK_DETAILS_CHANGED"


class Outcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class CheckResult:
    code: CheckCode
    outcome: Outcome
    details: dict[str, object]
    version: str = RULE_VERSION

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASS


@dataclass(frozen=True)
class SupplierRecord:
    id: str
    name: str
    aliases: tuple[str, ...]
    tax_id: str | None
    default_currency: str
    tax_rate_bp: int | None
    bank_account_hash: str | None


@dataclass(frozen=True)
class LineFacts:
    line_no: int
    quantity: Decimal | None
    unit_price_minor: int | None
    amount_minor: int | None
    tax_rate: Decimal | None  # percent (19 means 19%)


@dataclass(frozen=True)
class InvoiceFacts:
    currency: str | None
    printed_currency: str | None  # as printed, for when `currency` could not be settled
    invoice_date: date | None
    due_date: date | None
    subtotal_minor: int | None
    tax_minor: int | None
    total_minor: int | None
    lines: Sequence[LineFacts]
    supplier_name: str | None
    supplier_tax_id: str | None
    bank_account_hash: str | None  # keyed hash; never the account number


def _int_setting(raw: Mapping[str, object], key: str, default: int, low: int, high: int) -> int:
    v = raw.get(key)
    if isinstance(v, int) and not isinstance(v, bool) and low <= v <= high:
        return v
    return default


@dataclass(frozen=True)
class ValidationSettings:
    max_invoice_age_days: int = 365
    future_date_tolerance_days: int = 0
    line_tolerance_minor: int = 1  # per line, for line math and for the lines-sum check
    total_tolerance_minor: int = 1
    tax_tolerance_per_line_minor: int = 1
    supplier_fuzzy_min: int = 90  # a closest match at or above this is suggested (never trusted)

    @classmethod
    def from_tenant(cls, raw: Mapping[str, object]) -> "ValidationSettings":
        """Read the tenant's settings JSON. A missing or invalid value falls back to the default."""
        d = cls()
        return cls(
            **{
                name: _int_setting(raw, name, getattr(d, name), low, high)
                for name, (low, high) in _LIMITS.items()
            }
        )


_LIMITS = {
    "max_invoice_age_days": (0, 36500),
    "future_date_tolerance_days": (0, 365),
    "line_tolerance_minor": (0, 1000),
    "total_tolerance_minor": (0, 1000),
    "tax_tolerance_per_line_minor": (0, 1000),
    "supplier_fuzzy_min": (0, 100),
}


# ---- supplier matching ---------------------------------------------------------------------


@dataclass(frozen=True)
class SupplierMatch:
    supplier: SupplierRecord | None
    matched_by: str | None  # "tax_id" or "name"
    closest_name: str | None = None
    closest_score: float = 0.0


def _name_key(name: str) -> str:
    """Case, accents-as-written, punctuation and spacing folded away: 'ACME  Supplies, Ltd.'."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", folded).split())


def _tax_key(tax_id: str) -> str:
    return " ".join(tax_id.split()).upper()


def match_supplier(
    name: str | None, tax_id: str | None, suppliers: Sequence[SupplierRecord], fuzzy_min: int
) -> SupplierMatch:
    """A supplier is *known* only by an exact tax id or an exact name or alias (ignoring case and
    punctuation). A similar name is never enough on its own: a near-identical name from an
    unverified sender is a classic impersonation pattern. The closest match is returned so a person
    can be pointed at it."""
    if tax_id and tax_id.strip():
        wanted = _tax_key(tax_id)
        for s in suppliers:
            if s.tax_id and _tax_key(s.tax_id) == wanted:
                return SupplierMatch(s, "tax_id")
    if not name or not name.strip():
        return SupplierMatch(None, None)
    key = _name_key(name)
    best: tuple[float, SupplierRecord] | None = None
    for s in suppliers:
        candidates = [_name_key(n) for n in (s.name, *s.aliases)]
        if key in candidates:
            return SupplierMatch(s, "name")
        score = max((fuzz.token_sort_ratio(key, c) for c in candidates), default=0.0)
        if best is None or score > best[0]:
            best = (score, s)
    if best is None:
        return SupplierMatch(None, None)
    return SupplierMatch(None, None, best[1].name, round(best[0], 1))


# ---- rules ---------------------------------------------------------------------------------


def tax_of(base_minor: int, rate_bp: int) -> int:
    """Tax on an amount at a rate in basis points, rounded half up (symmetric for credit notes)."""
    sign = -1 if base_minor < 0 else 1
    return sign * ((abs(base_minor) * rate_bp + 5000) // 10000)


def _line_tax(amount_minor: int, rate_percent: Decimal) -> int:
    sign = -1 if amount_minor < 0 else 1
    cents = (Decimal(abs(amount_minor)) * rate_percent / 100).quantize(Decimal(1), ROUND_HALF_UP)
    return sign * int(cents)


def _dec(q: Decimal) -> str:
    return format(q.normalize(), "f")


def _result(code: CheckCode, outcome: Outcome, **details: object) -> CheckResult:
    return CheckResult(code, outcome, dict(details))


def _check_lines(f: InvoiceFacts, s: ValidationSettings) -> CheckResult:
    bad: list[dict[str, object]] = []
    unchecked: list[int] = []
    checked = 0
    for ln in f.lines:
        if ln.quantity is None or ln.unit_price_minor is None or ln.amount_minor is None:
            unchecked.append(ln.line_no)
            continue
        checked += 1
        expected = line_amount_minor(ln.quantity, ln.unit_price_minor)
        if abs(expected - ln.amount_minor) > s.line_tolerance_minor:
            bad.append(
                {
                    "line_no": ln.line_no,
                    "quantity": _dec(ln.quantity),
                    "unit_price_minor": ln.unit_price_minor,
                    "expected_minor": expected,
                    "amount_minor": ln.amount_minor,
                }
            )
    outcome = (
        Outcome.FAIL if bad else Outcome.PASS if checked and not unchecked else Outcome.SKIPPED
    )
    return _result(
        CheckCode.LINE_MATH_MISMATCH, outcome, checked=checked, lines=bad, unchecked_lines=unchecked
    )


def _check_totals(f: InvoiceFacts, s: ValidationSettings) -> CheckResult:
    checks: list[dict[str, object]] = []
    lines_sum: int | None = None
    amounts = [ln.amount_minor for ln in f.lines]
    if f.subtotal_minor is not None and amounts and all(a is not None for a in amounts):
        lines_sum = sum(a for a in amounts if a is not None)
        ok = abs(lines_sum - f.subtotal_minor) <= len(amounts) * s.line_tolerance_minor
        checks.append(
            {"name": "LINES_SUM_TO_SUBTOTAL", "ok": ok, "expected_minor": lines_sum,
             "actual_minor": f.subtotal_minor}
        )  # fmt: skip
    if f.subtotal_minor is not None and f.tax_minor is not None and f.total_minor is not None:
        expected = f.subtotal_minor + f.tax_minor
        ok = abs(expected - f.total_minor) <= s.total_tolerance_minor
        checks.append(
            {"name": "SUBTOTAL_PLUS_TAX_IS_TOTAL", "ok": ok, "expected_minor": expected,
             "actual_minor": f.total_minor}
        )  # fmt: skip
    outcome = (
        Outcome.SKIPPED if not checks
        else Outcome.PASS if all(c["ok"] for c in checks) else Outcome.FAIL
    )  # fmt: skip
    return _result(
        CheckCode.TOTAL_MISMATCH, outcome, lines_sum_minor=lines_sum,
        subtotal_minor=f.subtotal_minor, tax_minor=f.tax_minor, total_minor=f.total_minor,
        checks=checks,
    )  # fmt: skip


def _check_tax(f: InvoiceFacts, s: ValidationSettings, sup: SupplierRecord | None) -> CheckResult:
    if f.subtotal_minor is None or f.tax_minor is None:
        return _result(CheckCode.TAX_MISMATCH, Outcome.SKIPPED, reason="MISSING_AMOUNTS")
    expected: int
    source: str
    rate_bp: int | None = None
    if sup is not None and sup.tax_rate_bp is not None:
        rate_bp, expected, source = (
            sup.tax_rate_bp,
            tax_of(f.subtotal_minor, sup.tax_rate_bp),
            "supplier",
        )
    elif f.lines and all(ln.amount_minor is not None and ln.tax_rate is not None for ln in f.lines):
        expected = sum(
            _line_tax(ln.amount_minor, ln.tax_rate)
            for ln in f.lines
            if ln.amount_minor is not None and ln.tax_rate is not None
        )  # noqa: E501
        source = "lines"
    else:
        return _result(CheckCode.TAX_MISMATCH, Outcome.SKIPPED, reason="NO_RATE_TO_COMPARE")
    tolerance = max(1, len(f.lines)) * s.tax_tolerance_per_line_minor
    ok = abs(expected - f.tax_minor) <= tolerance
    return _result(
        CheckCode.TAX_MISMATCH, Outcome.PASS if ok else Outcome.FAIL, source=source,
        rate_bp=rate_bp, expected_minor=expected, actual_minor=f.tax_minor,
        subtotal_minor=f.subtotal_minor,
    )  # fmt: skip


def _check_dates(f: InvoiceFacts, s: ValidationSettings, today: date) -> CheckResult:
    if f.invoice_date is None:
        return _result(CheckCode.INVALID_DATE, Outcome.SKIPPED, reason="NO_INVOICE_DATE")
    reasons: list[dict[str, object]] = []
    inv = f.invoice_date
    if inv > today + timedelta(days=s.future_date_tolerance_days):
        reasons.append({"code": "FUTURE_INVOICE_DATE", "invoice_date": inv.isoformat()})
    if (today - inv).days > s.max_invoice_age_days:
        reasons.append(
            {"code": "INVOICE_TOO_OLD", "invoice_date": inv.isoformat(),
             "max_age_days": s.max_invoice_age_days}
        )  # fmt: skip
    if f.due_date is not None and f.due_date < inv:
        reasons.append(
            {"code": "DUE_BEFORE_INVOICE", "invoice_date": inv.isoformat(),
             "due_date": f.due_date.isoformat()}
        )  # fmt: skip
    return _result(
        CheckCode.INVALID_DATE,
        Outcome.FAIL if reasons else Outcome.PASS,
        reasons=reasons,
        today=today.isoformat(),
    )


def _check_currency(f: InvoiceFacts, sup: SupplierRecord | None) -> CheckResult:
    if sup is None:
        return _result(CheckCode.CURRENCY_MISMATCH, Outcome.SKIPPED, reason="UNKNOWN_SUPPLIER")
    details: dict[str, object] = {
        "invoice_currency": f.currency, "supplier_currency": sup.default_currency,
        "printed": f.printed_currency,
    }  # fmt: skip
    if f.currency is not None:
        outcome = Outcome.PASS if f.currency == sup.default_currency else Outcome.FAIL
        return CheckResult(CheckCode.CURRENCY_MISMATCH, outcome, details)
    printed = (f.printed_currency or "").strip()
    if printed in _SYMBOL_CURRENCY:  # a symbol that could not be settled on its own
        outcome = (
            Outcome.PASS if _SYMBOL_CURRENCY[printed] == sup.default_currency else Outcome.FAIL
        )
    elif re.fullmatch(r"[A-Za-z]{3}", printed):  # a code we do not support
        outcome = Outcome.PASS if printed.upper() == sup.default_currency else Outcome.FAIL
    else:
        outcome = Outcome.SKIPPED
    return CheckResult(CheckCode.CURRENCY_MISMATCH, outcome, details)


def _check_supplier(f: InvoiceFacts, m: SupplierMatch, s: ValidationSettings) -> CheckResult:
    if not (f.supplier_name and f.supplier_name.strip()) and not f.supplier_tax_id:
        return _result(CheckCode.UNKNOWN_SUPPLIER, Outcome.SKIPPED, reason="NOTHING_TO_MATCH")
    if m.supplier is not None:
        return _result(
            CheckCode.UNKNOWN_SUPPLIER,
            Outcome.PASS,
            supplier_id=m.supplier.id,
            matched_by=m.matched_by,
        )
    return _result(
        CheckCode.UNKNOWN_SUPPLIER, Outcome.FAIL, printed_name=f.supplier_name,
        printed_tax_id=f.supplier_tax_id, closest_name=m.closest_name,
        closest_score=m.closest_score, suggested=m.closest_score >= s.supplier_fuzzy_min,
    )  # fmt: skip


def _check_bank(f: InvoiceFacts, sup: SupplierRecord | None) -> CheckResult:
    code = CheckCode.BANK_DETAILS_CHANGED
    if sup is None:
        return _result(code, Outcome.SKIPPED, reason="UNKNOWN_SUPPLIER")
    if f.bank_account_hash is None:  # nothing printed, so nothing changed
        return _result(code, Outcome.PASS, supplier_id=sup.id, bank_on_invoice=False)
    if sup.bank_account_hash is None:
        return _result(code, Outcome.SKIPPED, supplier_id=sup.id, reason="NO_BANK_ON_FILE")
    same = hmac.compare_digest(f.bank_account_hash, sup.bank_account_hash)
    return _result(
        code, Outcome.PASS if same else Outcome.FAIL, supplier_id=sup.id, bank_on_invoice=True
    )


def validate(
    facts: InvoiceFacts,
    suppliers: Sequence[SupplierRecord],
    settings: ValidationSettings,
    today: date,
) -> tuple[SupplierMatch, list[CheckResult]]:
    """Run every check. `today` is passed in so the rules stay pure and tests stay stable."""
    match = match_supplier(
        facts.supplier_name, facts.supplier_tax_id, suppliers, settings.supplier_fuzzy_min
    )
    sup = match.supplier
    return match, [
        _check_lines(facts, settings),
        _check_totals(facts, settings),
        _check_tax(facts, settings, sup),
        _check_dates(facts, settings, today),
        _check_currency(facts, sup),
        _check_supplier(facts, match, settings),
        _check_bank(facts, sup),
    ]


__all__ = [
    "RULE_VERSION", "CheckCode", "CheckResult", "InvoiceFacts", "LineFacts", "Outcome",
    "SupplierMatch", "SupplierRecord", "ValidationSettings", "match_supplier", "tax_of", "validate",
]  # fmt: skip
