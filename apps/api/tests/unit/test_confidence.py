from datetime import date
from decimal import Decimal

import pytest

from intake.core.confidence import (
    CRITICAL_FIELDS,
    SelfConfidence,
    Signals,
    amount_in_text,
    date_in_text,
    fields_below_threshold,
    score_field,
    sum_supports,
    text_in_text,
)

D = Decimal


def sig(
    self_conf: SelfConfidence,
    text: bool | None = None,
    rule: bool | None = None,
    master: bool | None = None,
) -> Signals:
    return Signals(self_confidence=self_conf, text_layer=text, rule=rule, master_data=master)


def test_high_with_text_layer_agreement_is_trusted() -> None:
    assert score_field(sig(SelfConfidence.HIGH, text=True)).confidence >= D("0.8")


def test_high_alone_on_scanned_doc_is_not_trusted() -> None:
    # No text layer to check against and no other support: must not clear by itself.
    assert score_field(sig(SelfConfidence.HIGH)).confidence < D("0.8")


def test_high_with_rule_support_is_trusted_without_text_layer() -> None:
    assert score_field(sig(SelfConfidence.HIGH, rule=True)).confidence >= D("0.8")


def test_low_never_reaches_threshold_even_with_all_support() -> None:
    s = score_field(sig(SelfConfidence.LOW, text=True, rule=True, master=True))
    assert s.confidence < D("0.8")


def test_medium_needs_multiple_supports() -> None:
    assert score_field(sig(SelfConfidence.MEDIUM, text=True)).confidence < D("0.8")
    assert score_field(sig(SelfConfidence.MEDIUM, text=True, rule=True)).confidence >= D("0.8")


@pytest.mark.parametrize("which", ["text", "rule", "master"])
def test_any_contradiction_caps_below_threshold(which: str) -> None:
    kwargs: dict[str, bool | None] = {"text": True, "rule": True, "master": True, which: False}
    s = score_field(sig(SelfConfidence.HIGH, **kwargs))
    assert s.confidence < D("0.8")


def test_confidence_is_within_zero_and_one() -> None:
    top = score_field(sig(SelfConfidence.HIGH, text=True, rule=True, master=True)).confidence
    assert D(0) <= top <= D(1)


def test_signals_are_recorded_for_the_ui() -> None:
    s = score_field(sig(SelfConfidence.HIGH, text=True, rule=False))
    assert s.signals == {
        "self_confidence": "high",
        "text_layer": True,
        "rule": False,
        "master_data": None,
    }


def test_missing_value_has_zero_confidence() -> None:
    assert score_field(None).confidence == D(0)


def test_critical_fields_match_playbook() -> None:
    assert set(CRITICAL_FIELDS) == {
        "supplier_name",
        "invoice_number",
        "invoice_date",
        "total",
        "currency",
    }


def test_fields_below_threshold_lists_weak_and_missing_critical_fields() -> None:
    scores = {
        "supplier_name": D("0.9"),
        "invoice_number": D("0.79"),
        "invoice_date": D("0.8"),
        "total": D("0.95"),
        "po_number": D("0.1"),  # not critical: ignored
    }  # currency missing entirely
    assert fields_below_threshold(scores, CRITICAL_FIELDS, D("0.8")) == [
        "invoice_number",
        "currency",
    ]


def test_fields_below_threshold_empty_when_all_good() -> None:
    scores = {f: D("0.9") for f in CRITICAL_FIELDS}
    assert fields_below_threshold(scores, CRITICAL_FIELDS, D("0.8")) == []


# ---- text-layer agreement ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["Total: 1,234.56", "Total: 1.234,56 EUR", "Gesamt 1 234,56", "Total 1234.56", "TOTAL 1234,56"],
)
def test_amount_in_text_matches_any_number_format(text: str) -> None:
    assert amount_in_text(123456, 2, text)


def test_amount_in_text_no_partial_match_inside_bigger_number() -> None:
    assert not amount_in_text(123456, 2, "Ref 91234.567")
    assert not amount_in_text(123456, 2, "Total 11,234.56")


def test_amount_in_text_zero_decimal_currency() -> None:
    assert amount_in_text(1500, 0, "Total JPY 1,500")
    assert not amount_in_text(1500, 0, "Total JPY 15,000")


def test_amount_in_text_absent() -> None:
    assert not amount_in_text(123456, 2, "Total 99.00")


@pytest.mark.parametrize(
    "text",
    ["Date: 2026-03-13", "13/03/2026", "13.03.2026", "03/13/2026", "March 13, 2026", "13 Mar 2026", "13-Mar-2026"],
)
def test_date_in_text_matches_common_renderings(text: str) -> None:
    assert date_in_text(date(2026, 3, 13), text)


def test_date_in_text_absent() -> None:
    assert not date_in_text(date(2026, 3, 13), "Date: 2026-03-14")


def test_text_in_text_ignores_case_spacing_and_punctuation() -> None:
    assert text_in_text("RW-2026-0592", "Invoice No: rw 2026 0592")
    assert text_in_text("Rheinwerk Bürobedarf GmbH", "RHEINWERK  BÜROBEDARF GMBH\n697 Industrial")


def test_text_in_text_absent_or_empty() -> None:
    assert not text_in_text("RW-2026-0592", "Invoice No: RW-2026-0593")
    assert not text_in_text("", "anything")


# ---- rule support -----------------------------------------------------------------------


def test_sum_supports_true_within_tolerance() -> None:
    assert sum_supports(1000, [400, 600])
    assert sum_supports(1000, [400, 601], tolerance_minor=1)


def test_sum_supports_false_when_off() -> None:
    assert not sum_supports(1000, [400, 500])


def test_sum_supports_none_when_nothing_to_check() -> None:
    assert sum_supports(1000, []) is None
    assert sum_supports(None, [1000]) is None
