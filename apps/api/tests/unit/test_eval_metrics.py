from intake.core.eval_metrics import (
    FieldComparison,
    InvoiceEval,
    aggregate,
    exception_precision,
    exception_recall,
    false_clears,
    field_accuracy,
    line_metrics,
    touchless_rate,
)
from intake.core.statuses import DocQuality


def inv(**kw: object) -> InvoiceEval:
    base: dict[str, object] = {
        "doc_quality": DocQuality.CLEAN,
        "fields": (),
        "lines_actual": 0,
        "lines_expected": 0,
        "lines_matched": 0,
        "planted_codes": (),
        "raised_codes": (),
        "should_clear": True,
        "routed_straight_through": True,
        "cost_usd_micros": 0,
        "latency_ms": 0,
    }
    return InvoiceEval(**{**base, **kw})  # type: ignore[arg-type]


def fc(field: str, correct: bool) -> FieldComparison:
    return FieldComparison(field, correct)


def test_field_accuracy_sums_across_invoices_in_first_seen_order() -> None:
    evals = [
        inv(fields=(fc("total", True), fc("currency", False))),
        inv(fields=(fc("total", True), fc("currency", True))),
    ]
    rows = field_accuracy(evals)
    assert [r.field for r in rows] == ["total", "currency"]
    total_row, currency_row = rows
    assert (total_row.correct, total_row.total, total_row.accuracy) == (2, 2, 1.0)
    assert (currency_row.correct, currency_row.total, currency_row.accuracy) == (1, 2, 0.5)


def test_field_accuracy_of_no_invoices_is_empty() -> None:
    assert field_accuracy([]) == ()


def test_line_metrics_precision_recall_f1() -> None:
    # 8 actual lines, 6 matched; 10 expected lines, 6 matched
    evals = [
        inv(lines_actual=3, lines_expected=5, lines_matched=2),
        inv(lines_actual=5, lines_expected=5, lines_matched=4),
    ]
    m = line_metrics(evals)
    assert m.precision == 6 / 8
    assert m.recall == 6 / 10
    assert m.f1 == 2 * (6 / 8) * (6 / 10) / ((6 / 8) + (6 / 10))


def test_line_metrics_with_no_lines_at_all_is_none() -> None:
    m = line_metrics([inv()])
    assert (m.precision, m.recall, m.f1) == (None, None, None)


def test_exception_recall_counts_every_planted_problem() -> None:
    evals = [
        inv(planted_codes=("NO_PO",), raised_codes=("NO_PO",)),
        inv(planted_codes=("TOTAL_MISMATCH",), raised_codes=()),  # missed
        inv(planted_codes=(), raised_codes=("NO_PO",)),  # nothing planted here
    ]
    assert exception_recall(evals) == 1 / 2


def test_exception_recall_with_no_planted_problems_is_none() -> None:
    assert exception_recall([inv()]) is None


def test_exception_precision_counts_every_raised_exception() -> None:
    evals = [
        inv(planted_codes=("NO_PO",), raised_codes=("NO_PO", "TOTAL_MISMATCH")),
        inv(planted_codes=(), raised_codes=("NO_PO",)),
    ]
    # raised: NO_PO (real), TOTAL_MISMATCH (not planted on that invoice), NO_PO (not planted there)
    assert exception_precision(evals) == 1 / 3


def test_exception_precision_with_nothing_raised_is_none() -> None:
    assert exception_precision([inv()]) is None


def test_touchless_rate() -> None:
    evals = [
        inv(routed_straight_through=True),
        inv(routed_straight_through=True),
        inv(routed_straight_through=False),
    ]
    assert touchless_rate(evals) == 2 / 3


def test_touchless_rate_of_no_invoices_is_none() -> None:
    assert touchless_rate([]) is None


def test_false_clears_counts_only_straight_through_invoices_with_a_real_problem() -> None:
    evals = [
        inv(routed_straight_through=True, should_clear=True),  # correctly cleared
        inv(routed_straight_through=True, should_clear=False),  # FALSE CLEAR
        inv(routed_straight_through=False, should_clear=False),  # sent to review, not a false clear
    ]
    count, rate = false_clears(evals)
    assert count == 1
    assert rate == 1 / 2  # of the 2 routed straight through


def test_false_clears_with_none_routed_straight_through_is_zero_count_none_rate() -> None:
    count, rate = false_clears([inv(routed_straight_through=False)])
    assert (count, rate) == (0, None)


def test_aggregate_splits_fields_by_doc_quality() -> None:
    evals = [
        inv(doc_quality=DocQuality.CLEAN, fields=(fc("total", True),)),
        inv(doc_quality=DocQuality.SCANNED, fields=(fc("total", False),)),
    ]
    m = aggregate(evals)
    assert m.invoice_count == 2
    assert m.fields_by_quality[DocQuality.CLEAN][0].accuracy == 1.0
    assert m.fields_by_quality[DocQuality.SCANNED][0].accuracy == 0.0
    assert m.fields[0].accuracy == 0.5  # overall


def test_aggregate_mean_cost_and_latency_percentiles() -> None:
    evals = [
        inv(cost_usd_micros=1000, latency_ms=100),
        inv(cost_usd_micros=2000, latency_ms=200),
        inv(cost_usd_micros=3000, latency_ms=300),
        inv(cost_usd_micros=4000, latency_ms=400),
    ]
    m = aggregate(evals)
    assert m.mean_cost_usd_micros == 2500
    assert m.latency_p50_ms == 200
    assert m.latency_p95_ms == 400


def test_aggregate_of_no_invoices_has_no_rates() -> None:
    m = aggregate([])
    assert m.invoice_count == 0
    assert m.touchless_rate is None
    assert m.false_clear_rate is None
    assert m.false_clears == 0
    assert m.mean_cost_usd_micros is None
    assert (m.latency_p50_ms, m.latency_p95_ms) == (None, None)
