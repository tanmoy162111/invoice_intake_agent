"""Field confidence (playbook §6.3): combine the model's self-report with independent signals.

The model's own confidence is never trusted alone. A field reaches the review threshold only with
corroboration, and any contradicting signal caps it below the threshold. Uncertain means human.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

CRITICAL_FIELDS: tuple[str, ...] = (
    "supplier_name",
    "invoice_number",
    "invoice_date",
    "total",
    "currency",
)

_ZERO, _ONE = Decimal(0), Decimal(1)
_CONTRADICTED_CAP = Decimal("0.4")


class SelfConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


_BASE = {
    SelfConfidence.HIGH: Decimal("0.75"),
    SelfConfidence.MEDIUM: Decimal("0.55"),
    SelfConfidence.LOW: Decimal("0.30"),
}
_BONUS = {"text_layer": Decimal("0.15"), "rule": Decimal("0.10"), "master_data": Decimal("0.10")}


@dataclass(frozen=True)
class Signals:
    """Each support signal is True (supports), False (contradicts) or None (not applicable)."""

    self_confidence: SelfConfidence
    text_layer: bool | None = None
    rule: bool | None = None
    master_data: bool | None = None


@dataclass(frozen=True)
class FieldScore:
    confidence: Decimal
    signals: dict[str, object]


def score_field(signals: Signals | None) -> FieldScore:
    """0-1 confidence plus the signals behind it. No signals (value missing) scores 0."""
    if signals is None:
        return FieldScore(_ZERO, {})
    supports = {
        "text_layer": signals.text_layer,
        "rule": signals.rule,
        "master_data": signals.master_data,
    }
    score = _BASE[signals.self_confidence]
    for name, value in supports.items():
        if value:
            score += _BONUS[name]
    if any(v is False for v in supports.values()):
        score = min(score, _CONTRADICTED_CAP)
    return FieldScore(
        min(score, _ONE),
        {"self_confidence": signals.self_confidence.value, **supports},
    )


def fields_below_threshold(
    scores: Mapping[str, Decimal], critical: Sequence[str], minimum: Decimal
) -> list[str]:
    """Critical fields that are missing or below `minimum`, in `critical` order."""
    return [f for f in critical if scores.get(f, _ZERO) < minimum]


# ---- text-layer agreement -----------------------------------------------------------------

_GROUP_SEPS = ("", ",", ".", " ", " ", "'")
_DECIMAL_SEPS = (".", ",")


def _group(digits: str, sep: str) -> str:
    if not sep:
        return digits
    head = len(digits) % 3 or 3
    parts = [digits[:head]] + [digits[i : i + 3] for i in range(head, len(digits), 3)]
    return sep.join(parts)


def amount_in_text(minor: int, exponent: int, text: str) -> bool:
    """Does the amount appear in `text` in any common number format?"""
    whole, frac = divmod(abs(minor), 10**exponent)
    candidates: set[str] = set()
    for gsep in _GROUP_SEPS:
        grouped = _group(str(whole), gsep)
        if exponent == 0:
            candidates.add(grouped)
            continue
        for dsep in _DECIMAL_SEPS:
            if dsep != gsep:
                candidates.add(f"{grouped}{dsep}{frac:0{exponent}d}")
    pattern = "|".join(re.escape(c) for c in sorted(candidates, key=len, reverse=True))
    return re.search(rf"(?<!\d)(?<!\d[.,])(?:{pattern})(?!\d)(?![.,]\d)", text) is not None


_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)  # fmt: skip


def date_in_text(value: date, text: str) -> bool:
    """Does the date appear in `text` in any common rendering?"""
    d, m, y = value.day, value.month, value.year
    month = _MONTH_NAMES[m - 1]
    forms: set[str] = {f"{y}-{m:02d}-{d:02d}"}
    for dd in {str(d), f"{d:02d}"}:
        for mm in {str(m), f"{m:02d}"}:
            for sep in "/.-":
                forms.add(f"{dd}{sep}{mm}{sep}{y}")
                forms.add(f"{mm}{sep}{dd}{sep}{y}")
        for name in (month, month[:3]):
            forms.add(f"{dd} {name} {y}")
            forms.add(f"{name} {dd}, {y}")
            forms.add(f"{name} {dd} {y}")
    pattern = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    return re.search(rf"(?<!\d)(?:{pattern})(?!\d)", text, re.IGNORECASE) is not None


def _squash(s: str) -> str:
    folded = unicodedata.normalize("NFKC", s).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded).split())


def text_in_text(value: str, text: str) -> bool:
    """Case, spacing and punctuation-insensitive whole-token containment."""
    needle = _squash(value)
    if not needle:
        return False
    return f" {needle} " in f" {_squash(text)} "


# ---- rule support -------------------------------------------------------------------------


def sum_supports(total: int | None, parts: Sequence[int], tolerance_minor: int = 0) -> bool | None:
    """Do `parts` add up to `total`? None when there is nothing to check."""
    if total is None or not parts:
        return None
    return abs(sum(parts) - total) <= tolerance_minor
