"""Duplicate-invoice detection (playbook §6.5). Pure: an invoice and earlier ones in, a result out.

A *hard* duplicate is the same supplier with the same invoice number once punctuation, spacing,
case and leading zeros are ignored. A *soft* duplicate is the same supplier and the same signed
total and currency, an invoice date within a window, and a very similar number (this catches
INV-1043, INV1043 and 1043). Only the later invoice is compared with earlier ones; the original is
never flagged. When the comparison cannot be done (no supplier, no number, the data the soft rule
needs is missing on an otherwise similar invoice, or the supplier cannot be reconciled) the result
is `skipped`, never a pass.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from rapidfuzz import fuzz

from intake.core.validate import Outcome
from intake.core.validate import _int_setting as int_setting
from intake.core.validate import _name_key as name_key

RULE_VERSION = "v1"
POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
_MIN_DIGITS = 4  # fewer digits than this say nothing about whether two numbers are the same
_DIFFERENT_COMPANY_BELOW = 60  # printed names less alike than this are different companies


class DuplicateKind(StrEnum):
    HARD = "hard"
    SOFT = "soft"


@dataclass(frozen=True)
class InvoiceRef:
    id: str
    supplier_id: str | None  # the linked supplier record, when the supplier is known
    supplier_name: str | None  # the name as printed
    invoice_number: str | None
    invoice_date: date | None
    total_minor: int | None
    currency: str | None


@dataclass(frozen=True)
class DedupeSettings:
    window_days: int = 7
    number_similarity_min: int = 85

    @classmethod
    def from_tenant(cls, raw: Mapping[str, object]) -> "DedupeSettings":
        d = cls()
        return cls(
            window_days=int_setting(raw, "dedupe_window_days", d.window_days, 0, 365),
            number_similarity_min=int_setting(
                raw, "dedupe_number_similarity_min", d.number_similarity_min, 0, 100
            ),
        )


@dataclass(frozen=True)
class DuplicateMatch:
    kind: DuplicateKind
    existing: InvoiceRef
    similarity: float
    days_apart: int | None


@dataclass(frozen=True)
class DedupeResult:
    outcome: Outcome
    match: DuplicateMatch | None
    details: dict[str, object]
    version: str = RULE_VERSION

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASS


def number_key(number: str) -> str:
    """Upper case letters and digits only: 'inv 1043' and 'INV-1043' are the same number."""
    folded = unicodedata.normalize("NFKC", number).upper()
    return "".join(ch for ch in folded if ch.isalnum())


def supplier_name_key(name: str | None) -> str | None:
    """The printed supplier name with case, punctuation and spacing folded away, or None."""
    key = name_key(name) if name and name.strip() else ""
    return key or None


def canonical_key(key: str) -> str:
    """A number key with leading zeros removed from every run of digits (INV-0043 == INV-43)."""
    return re.sub(r"\d+", lambda m: m.group().lstrip("0") or "0", key)


def _same_supplier(a: InvoiceRef, b: InvoiceRef) -> bool | None:
    """True, False, or None when the two cannot be told apart from what is recorded.

    Two linked invoices are the same supplier exactly when their supplier records are the same. When
    a record is missing on either side the printed names decide; a linked and an unlinked invoice
    whose printed names could be one company written two ways cannot be reconciled, so the answer is
    None (uncertain)."""
    ia = (a.supplier_id or "").strip() or None
    ib = (b.supplier_id or "").strip() or None
    if ia and ib:
        return ia == ib
    na, nb = supplier_name_key(a.supplier_name), supplier_name_key(b.supplier_name)
    if na and nb:
        if na == nb:
            return True
        if not (ia or ib) or fuzz.token_sort_ratio(na, nb) < _DIFFERENT_COMPANY_BELOW:
            return False  # two unknown senders with other names, or clearly different companies
        return None  # could be one company written two ways
    return None


def _digits(key: str) -> str:
    return "".join(ch for ch in key if ch.isdigit())


def number_similarity(a: str, b: str) -> float:
    """0 to 100. The whole normalized numbers, or their digits when both have enough of them."""
    ka, kb = number_key(a), number_key(b)
    score = max(float(fuzz.ratio(ka, kb)), float(fuzz.ratio(canonical_key(ka), canonical_key(kb))))
    da, db = _digits(ka), _digits(kb)
    if len(da) >= _MIN_DIGITS and len(db) >= _MIN_DIGITS:
        score = max(score, float(fuzz.ratio(da, db)))
    return score


def _clip(text: str | None, limit: int = 100) -> str | None:
    return text if text is None or len(text) <= limit else text[:limit]


def _complete(x: InvoiceRef) -> bool:
    """Has what the soft rule compares. A zero total is not evidence: any two placeholders match."""
    return bool(x.total_minor) and x.invoice_date is not None and bool(x.currency)


def _details(match: DuplicateMatch, compared: int) -> dict[str, object]:
    e = match.existing
    return {
        "kind": match.kind.value,
        "existing_invoice_id": e.id,
        "existing_invoice_number": _clip(e.invoice_number),
        "existing_invoice_date": e.invoice_date.isoformat() if e.invoice_date else None,
        "similarity": round(match.similarity, 1),
        "days_apart": match.days_apart,
        "compared": compared,
    }


def _days(a: date | None, b: date | None) -> int | None:
    return abs((a - b).days) if a is not None and b is not None else None


def check_duplicate(
    new: InvoiceRef, earlier: Sequence[InvoiceRef], settings: DedupeSettings
) -> DedupeResult:
    """Is `new` a duplicate of any invoice in `earlier`?

    The caller passes only invoices received before `new`, in received order; this function does not
    check receipt order itself."""
    if supplier_name_key(new.supplier_name) is None and not (new.supplier_id or "").strip():
        return DedupeResult(Outcome.SKIPPED, None, {"reason": "NO_SUPPLIER"})
    new_key = number_key(new.invoice_number or "")
    if not new_key:
        return DedupeResult(Outcome.SKIPPED, None, {"reason": "NO_INVOICE_NUMBER"})
    new_canon = canonical_key(new_key)

    same: list[InvoiceRef] = []
    supplier_uncertain = False
    for e in earlier:
        if e.id == new.id:
            continue
        relation = _same_supplier(new, e)
        if relation is False:
            continue
        ek = number_key(e.invoice_number or "")
        if relation is None:  # matters only if the numbers point at each other
            if ek and (
                canonical_key(ek) == new_canon
                or number_similarity(new.invoice_number or "", e.invoice_number or "")
                >= settings.number_similarity_min
            ):
                supplier_uncertain = True
            continue
        same.append(e)

    for e in same:
        if canonical_key(number_key(e.invoice_number or "")) == new_canon:
            days = _days(new.invoice_date, e.invoice_date)
            match = DuplicateMatch(DuplicateKind.HARD, e, 100.0, days)
            return DedupeResult(Outcome.FAIL, match, _details(match, len(same)))

    best: DuplicateMatch | None = None
    unevaluable = False
    for e in same:
        if not number_key(e.invoice_number or ""):
            continue
        sim = number_similarity(new.invoice_number or "", e.invoice_number or "")
        if sim < settings.number_similarity_min:
            continue
        if not (_complete(new) and _complete(e)):
            unevaluable = True  # similar number, but the soft rule has nothing to compare
            continue
        days = _days(new.invoice_date, e.invoice_date)
        if (
            (e.currency or "").upper() == (new.currency or "").upper()
            and e.total_minor == new.total_minor
            and days is not None
            and days <= settings.window_days
            and (best is None or sim > best.similarity)
        ):
            best = DuplicateMatch(DuplicateKind.SOFT, e, sim, days)
    if best is not None:
        return DedupeResult(Outcome.FAIL, best, _details(best, len(same)))
    if unevaluable:
        return DedupeResult(
            Outcome.SKIPPED, None, {"reason": "INCOMPLETE_FOR_SOFT_MATCH", "compared": len(same)}
        )
    if supplier_uncertain:
        return DedupeResult(
            Outcome.SKIPPED, None, {"reason": "SUPPLIER_UNCERTAIN", "compared": len(same)}
        )
    return DedupeResult(Outcome.PASS, None, {"compared": len(same)})
