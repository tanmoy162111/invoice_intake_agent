from datetime import UTC, datetime

from intake.core.eval_metrics import InvoiceEval, aggregate
from intake.core.statuses import DocQuality
from intake.eval_report import RunInfo, render_report


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
        "cost_usd_micros": 1000,
        "latency_ms": 500,
    }
    return InvoiceEval(**{**base, **kw})  # type: ignore[arg-type]


def run(**kw: object) -> RunInfo:
    base: dict[str, object] = {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "prompt_version": "v1",
        "git_sha": "abc1234",
        "generated_at": datetime(2026, 9, 28, tzinfo=UTC),
    }
    return RunInfo(**{**base, **kw})  # type: ignore[arg-type]


def test_report_has_the_date_model_provider_and_invoice_count() -> None:
    text = render_report(aggregate([inv(), inv()]), run())
    assert "# Evaluation report — 2026-09-28" in text
    assert "`claude-sonnet-5`" in text and "`anthropic`" in text and "`v1`" in text
    assert "`abc1234`" in text
    assert "Invoices: 2" in text


def test_no_non_reference_banner_for_anthropic() -> None:
    text = render_report(aggregate([inv()]), run(provider="anthropic"))
    assert "Non-reference build" not in text


def test_non_reference_banner_for_openrouter() -> None:
    text = render_report(aggregate([inv()]), run(provider="openrouter", model="qwen/x"))
    assert "⚠ **Non-reference build.**" in text
    assert "`openrouter`" in text and "`qwen/x`" in text
    assert "ADR 0010" in text


def test_false_clear_rate_and_gate_fail_shown() -> None:
    evals = [
        inv(routed_straight_through=True, should_clear=False),
        inv(routed_straight_through=True, should_clear=True),
    ]
    text = render_report(aggregate(evals), run())
    assert "False clear rate** (must be 0%) | 50.0% (1 invoice(s))" in text
    assert "Regression gate: FAIL." in text


def test_zero_false_clears_shown_as_gate_pass() -> None:
    text = render_report(aggregate([inv(routed_straight_through=True, should_clear=True)]), run())
    assert "False clear rate** (must be 0%) | 0.0% (0 invoice(s))" in text
    assert "Regression gate: pass" in text


def test_field_accuracy_table_and_by_quality_sections() -> None:
    from intake.core.eval_metrics import FieldComparison

    evals = [
        inv(doc_quality=DocQuality.CLEAN, fields=(FieldComparison("total", True),)),
        inv(doc_quality=DocQuality.SCANNED, fields=(FieldComparison("total", False),)),
    ]
    text = render_report(aggregate(evals), run())
    assert "## Field accuracy (overall)" in text
    assert "| `total` | 50.0% | 1 / 2 |" in text
    assert "### clean" in text and "### scanned" in text
    assert "### photo" not in text  # no photo invoices in this run: section omitted


def test_missing_metrics_render_as_a_dash_not_a_crash() -> None:
    text = render_report(aggregate([]), run())
    assert "Invoices: 0" in text
    assert text.count("—") >= 1  # touchless rate, cost, latency etc. all undefined


def test_cost_and_latency_are_formatted() -> None:
    evals = [inv(cost_usd_micros=3_100, latency_ms=850)]
    text = render_report(aggregate(evals), run())
    assert "$0.0031" in text
    assert "850 ms" in text
