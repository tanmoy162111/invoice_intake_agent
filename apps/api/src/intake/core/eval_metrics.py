"""Evaluation metrics (playbook §8.2): pure aggregation over one invoice's comparison against its
golden-set ground truth. No DB, network, or file I/O here - `eval/run_eval.py` gathers the facts
(from the real pipeline and `data/golden/truth/*.json`) and this module turns them into numbers.

The false clear rate is the number that matters most: an invoice routed straight through
(`route == "straight_through"`) that the ground truth says actually had a problem. It must be 0%.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from intake.core.extraction import Interpreted
from intake.core.statuses import DocQuality

# Ground-truth header key -> how to read the matching value off `Interpreted`. The three money
# fields and the two dates have their own typed attribute; everything else is read from
# `interpreted.fields` (by extraction schema field name) since `Interpreted` has no top-level
# attribute for it (supplier_tax_id, supplier_address, supplier_bank_account).
_DIRECT_HEADER_FIELDS: tuple[str, ...] = (
    "supplier_name", "invoice_number", "po_number", "payment_terms", "currency",
)  # fmt: skip
_DATE_HEADER_FIELDS: tuple[str, ...] = ("invoice_date", "due_date")
# truth key -> Interpreted attribute (only where the names differ)
_MONEY_HEADER_FIELDS: dict[str, str] = {
    "subtotal_minor": "subtotal_minor",
    "tax_total_minor": "tax_minor",
    "total_minor": "total_minor",
}
_VIA_FIELDS: tuple[str, ...] = ("supplier_tax_id", "supplier_address", "supplier_bank_account")


@dataclass(frozen=True)
class FieldComparison:
    """One field, on one invoice: did the normalized actual value exactly match the ground truth
    (including both being absent)? The comparison itself happens in the caller, which has both
    typed values; this only carries the verdict."""

    field: str
    correct: bool


@dataclass(frozen=True)
class InvoiceEval:
    """Everything one invoice contributes to the metrics, already compared against its truth."""

    doc_quality: DocQuality
    fields: tuple[FieldComparison, ...]
    lines_actual: int  # lines the pipeline produced
    lines_expected: int  # lines the ground truth has
    lines_matched: int  # actual lines whose amount and quantity both match some expected line
    planted_codes: tuple[str, ...]  # one entry per planted problem (playbook §8.1), by code
    raised_codes: tuple[str, ...]  # exception codes actually raised on this invoice
    should_clear: bool  # ground truth: does this invoice truly have no problem?
    routed_straight_through: bool  # actual: did routing send it straight through?
    cost_usd_micros: int
    latency_ms: int


@dataclass(frozen=True)
class FieldAccuracy:
    field: str
    correct: int
    total: int

    @property
    def accuracy(self) -> float | None:
        return None if self.total == 0 else self.correct / self.total


@dataclass(frozen=True)
class LineMetrics:
    precision: float | None
    recall: float | None
    f1: float | None


@dataclass(frozen=True)
class Metrics:
    invoice_count: int
    fields: tuple[FieldAccuracy, ...]  # overall, one per field
    fields_by_quality: Mapping[DocQuality, tuple[FieldAccuracy, ...]]
    lines: LineMetrics
    exception_recall: float | None  # of planted problems, how many raised the right code
    exception_precision: float | None  # of raised exceptions, how many were real problems
    touchless_rate: float | None  # share of invoices routed straight through
    false_clear_rate: float | None  # of straight-through invoices, how many actually had a problem
    false_clears: int  # the count itself: any number above 0 fails the gate, whatever the rate says
    mean_cost_usd_micros: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _percentile(values: Sequence[int], p: float) -> float | None:
    """Nearest-rank percentile (0 <= p <= 1) of unsorted values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(p * len(ordered))
    index = min(len(ordered) - 1, max(0, rank - 1))
    return float(ordered[index])


def field_accuracy(evals: Sequence[InvoiceEval]) -> tuple[FieldAccuracy, ...]:
    """One row per field name, in first-seen order, summed across every invoice given."""
    order: list[str] = []
    correct: dict[str, int] = {}
    total: dict[str, int] = {}
    for e in evals:
        for c in e.fields:
            if c.field not in total:
                order.append(c.field)
                correct[c.field] = 0
                total[c.field] = 0
            total[c.field] += 1
            correct[c.field] += 1 if c.correct else 0
    return tuple(FieldAccuracy(f, correct[f], total[f]) for f in order)


def line_metrics(evals: Sequence[InvoiceEval]) -> LineMetrics:
    actual = sum(e.lines_actual for e in evals)
    expected = sum(e.lines_expected for e in evals)
    matched = sum(e.lines_matched for e in evals)
    precision = _rate(matched, actual)
    recall = _rate(matched, expected)
    f1 = (
        None
        if precision is None or recall is None or precision + recall == 0
        else 2 * precision * recall / (precision + recall)
    )
    return LineMetrics(precision, recall, f1)


def exception_recall(evals: Sequence[InvoiceEval]) -> float | None:
    planted = 0
    caught = 0
    for e in evals:
        raised = set(e.raised_codes)
        for code in e.planted_codes:
            planted += 1
            if code in raised:
                caught += 1
    return _rate(caught, planted)


def exception_precision(evals: Sequence[InvoiceEval]) -> float | None:
    raised_total = 0
    real = 0
    for e in evals:
        planted = set(e.planted_codes)
        for code in e.raised_codes:
            raised_total += 1
            if code in planted:
                real += 1
    return _rate(real, raised_total)


def touchless_rate(evals: Sequence[InvoiceEval]) -> float | None:
    return _rate(sum(1 for e in evals if e.routed_straight_through), len(evals))


def false_clears(evals: Sequence[InvoiceEval]) -> tuple[int, float | None]:
    """(count, rate). The count is what the regression gate checks; the rate is for the report."""
    straight_through = [e for e in evals if e.routed_straight_through]
    bad = sum(1 for e in straight_through if not e.should_clear)
    return bad, _rate(bad, len(straight_through))


def aggregate(evals: Sequence[InvoiceEval]) -> Metrics:
    """Every metric in playbook §8.2, overall and split by `doc_quality`."""
    by_quality: dict[DocQuality, list[InvoiceEval]] = {}
    for e in evals:
        by_quality.setdefault(e.doc_quality, []).append(e)
    bad, fc_rate = false_clears(evals)
    costs = [e.cost_usd_micros for e in evals]
    latencies = [e.latency_ms for e in evals]
    return Metrics(
        invoice_count=len(evals),
        fields=field_accuracy(evals),
        fields_by_quality={q: field_accuracy(es) for q, es in by_quality.items()},
        lines=line_metrics(evals),
        exception_recall=exception_recall(evals),
        exception_precision=exception_precision(evals),
        touchless_rate=touchless_rate(evals),
        false_clear_rate=fc_rate,
        false_clears=bad,
        mean_cost_usd_micros=None if not costs else sum(costs) / len(costs),
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
    )


def _matched_line_count(actual: Sequence[Any], expected: Sequence[Mapping[str, Any]]) -> int:
    """How many expected lines have some actual line with the same amount and quantity (playbook
    §8.2: "a line matches if its amount and quantity are correct"). Each actual line is used for at
    most one match, so a duplicated line cannot inflate the count."""
    remaining = list(actual)
    matched = 0
    for exp in expected:
        exp_qty = Decimal(str(exp["quantity"]))
        for i, act in enumerate(remaining):
            if act.amount_minor == exp["amount_minor"] and act.quantity == exp_qty:
                matched += 1
                del remaining[i]
                break
    return matched


def compare_invoice(
    *,
    doc_quality: DocQuality,
    interpreted: Interpreted,
    truth: Mapping[str, Any],
    raised_codes: Sequence[str],
    routed_straight_through: bool,
    cost_usd_micros: int,
    latency_ms: int,
) -> InvoiceEval:
    """Turn one invoice's real pipeline output, next to its `data/golden/truth/*.json`, into the
    `InvoiceEval` that `aggregate` needs. The only decision here is what counts as "correct" for a
    field: exact match after normalization (playbook §8.2), on the same typed value the pipeline
    itself decides with (an int of minor units, a `date`, or normalized text) - never a raw string
    comparison of something formatted for display.
    """
    header = truth["header"]
    by_name = {f.field: f for f in interpreted.fields}
    comparisons = [
        FieldComparison(name, getattr(interpreted, name) == header[name])
        for name in _DIRECT_HEADER_FIELDS
    ]
    comparisons += [
        FieldComparison(
            name,
            getattr(interpreted, name)
            == (date.fromisoformat(header[name]) if header[name] else None),
        )
        for name in _DATE_HEADER_FIELDS
    ]
    comparisons += [
        FieldComparison(truth_key, getattr(interpreted, attr) == header[truth_key])
        for truth_key, attr in _MONEY_HEADER_FIELDS.items()
    ]
    comparisons += [
        FieldComparison(
            name, (by_name[name].normalized if name in by_name else None) == header[name]
        )
        for name in _VIA_FIELDS
    ]
    expected_lines = truth["lines"]
    return InvoiceEval(
        doc_quality=doc_quality,
        fields=tuple(comparisons),
        lines_actual=len(interpreted.lines),
        lines_expected=len(expected_lines),
        lines_matched=_matched_line_count(interpreted.lines, expected_lines),
        planted_codes=tuple(p["code"] for p in truth.get("planted") or ()),
        raised_codes=tuple(raised_codes),
        should_clear=bool(truth["expected"]["should_clear"]),
        routed_straight_through=routed_straight_through,
        cost_usd_micros=cost_usd_micros,
        latency_ms=latency_ms,
    )
