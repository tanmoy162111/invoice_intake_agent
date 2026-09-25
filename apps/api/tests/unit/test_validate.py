from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from intake.core.validate import (
    RULE_VERSION,
    CheckCode,
    CheckResult,
    InvoiceFacts,
    LineFacts,
    Outcome,
    SupplierRecord,
    ValidationSettings,
    match_supplier,
    tax_of,
    validate,
)

D = Decimal
TODAY = date(2026, 9, 25)

ACME = SupplierRecord(
    id="s1", name="Acme Supplies Ltd.", aliases=("Acme Supplies",), tax_id="69-9397618",
    default_currency="USD", tax_rate_bp=800, bank_account_hash="H-ACME",
)  # fmt: skip
COASTAL = SupplierRecord(
    id="s2", name="Coastal Packaging Ltd.", aliases=("Coastal Pack",), tax_id="12-3456789",
    default_currency="USD", tax_rate_bp=700, bank_account_hash="H-COASTAL",
)  # fmt: skip
SUPPLIERS = [ACME, COASTAL]


def line(n: int, qty: str, unit: int, amount: int, rate: str | None = None) -> LineFacts:
    return LineFacts(n, D(qty), unit, amount, D(rate) if rate else None)


def facts(**over: object) -> InvoiceFacts:
    base: dict[str, object] = {
        "currency": "USD", "printed_currency": "$",
        "invoice_date": date(2026, 9, 1), "due_date": date(2026, 10, 1),
        "subtotal_minor": 102400, "tax_minor": 8192, "total_minor": 110592,
        "lines": (line(1, "10", 1290, 12900), line(2, "10", 8950, 89500)),
        "supplier_name": "Acme Supplies Ltd.", "supplier_tax_id": "69-9397618",
        "bank_account_hash": "H-ACME",
    }  # fmt: skip
    return InvoiceFacts(**{**base, **over})  # type: ignore[arg-type]


def run(
    f: InvoiceFacts | None = None, settings: ValidationSettings | None = None
) -> dict[str, CheckResult]:
    _, results = validate(f or facts(), SUPPLIERS, settings or ValidationSettings(), TODAY)
    return {r.code.value: r for r in results}


def outcome(code: CheckCode, **over: object) -> Outcome:
    return run(facts(**over))[code.value].outcome


# ---- shape -------------------------------------------------------------------------------


def test_every_check_runs_once_with_a_version() -> None:
    results = run()
    assert set(results) == {c.value for c in CheckCode}
    assert all(r.version == RULE_VERSION for r in results.values())


def test_a_clean_invoice_passes_everything() -> None:
    assert {r.outcome for r in run().values()} == {Outcome.PASS}


def test_passed_is_true_only_for_a_pass() -> None:
    assert run()["TOTAL_MISMATCH"].passed is True
    assert run(facts(total_minor=1))["TOTAL_MISMATCH"].passed is False
    assert (
        run(facts(subtotal_minor=None))["TOTAL_MISMATCH"].passed is False
    )  # skipped is not a pass


def test_details_are_json_serializable() -> None:
    import json

    for r in run(
        facts(total_minor=1, invoice_date=date(2031, 1, 1), bank_account_hash="X")
    ).values():
        json.dumps(r.details)


# ---- line math ---------------------------------------------------------------------------


def test_line_math_fails_with_the_real_numbers() -> None:
    bad = (line(1, "12", 4500, 50400), line(2, "1", 100, 100))
    r = run(facts(lines=bad, subtotal_minor=50500))["LINE_MATH_MISMATCH"]
    assert r.outcome is Outcome.FAIL
    assert r.details["lines"] == [
        {"line_no": 1, "quantity": "12", "unit_price_minor": 4500, "expected_minor": 54000,
         "amount_minor": 50400}
    ]  # fmt: skip


def test_line_math_tolerates_one_minor_unit_per_line() -> None:
    assert outcome(CheckCode.LINE_MATH_MISMATCH, lines=(line(1, "3", 333, 1000),)) is Outcome.PASS
    assert outcome(CheckCode.LINE_MATH_MISMATCH, lines=(line(1, "3", 333, 1001),)) is Outcome.FAIL


def test_line_math_rounds_half_up_for_fractional_quantities() -> None:
    # 2.5 x 1.05 = 2.625 -> 263
    assert outcome(CheckCode.LINE_MATH_MISMATCH, lines=(line(1, "2.5", 105, 263),)) is Outcome.PASS


def test_line_math_credit_note_lines_are_checked_too() -> None:
    assert outcome(CheckCode.LINE_MATH_MISMATCH, lines=(line(1, "-2", 500, -1000),)) is Outcome.PASS
    assert outcome(CheckCode.LINE_MATH_MISMATCH, lines=(line(1, "-2", 500, 1000),)) is Outcome.FAIL


def test_line_math_is_skipped_when_a_line_cannot_be_read() -> None:
    unreadable = LineFacts(1, D("2"), None, 1000, None)
    r = run(facts(lines=(unreadable, line(2, "1", 100, 100))))["LINE_MATH_MISMATCH"]
    assert r.outcome is Outcome.SKIPPED and r.details["unchecked_lines"] == [1]


def test_line_math_failure_wins_over_an_unreadable_line() -> None:
    lines = (LineFacts(1, D("2"), None, 1000, None), line(2, "2", 100, 500))
    assert outcome(CheckCode.LINE_MATH_MISMATCH, lines=lines) is Outcome.FAIL


def test_line_math_with_no_lines_is_skipped() -> None:
    assert outcome(CheckCode.LINE_MATH_MISMATCH, lines=()) is Outcome.SKIPPED


# ---- totals ------------------------------------------------------------------------------


def test_total_fails_when_lines_do_not_sum_to_the_subtotal() -> None:
    r = run(facts(subtotal_minor=105000, total_minor=113192))["TOTAL_MISMATCH"]
    assert r.outcome is Outcome.FAIL
    assert r.details["lines_sum_minor"] == 102400 and r.details["subtotal_minor"] == 105000


def test_total_fails_when_subtotal_plus_tax_is_not_the_total() -> None:
    r = run(facts(total_minor=120000))["TOTAL_MISMATCH"]
    assert r.outcome is Outcome.FAIL
    failed = [c for c in r.details["checks"] if not c["ok"]]  # type: ignore[attr-defined]
    assert [c["name"] for c in failed] == ["SUBTOTAL_PLUS_TAX_IS_TOTAL"]


def test_total_tolerates_rounding_of_one_minor_unit() -> None:
    assert outcome(CheckCode.TOTAL_MISMATCH, total_minor=110593) is Outcome.PASS
    assert outcome(CheckCode.TOTAL_MISMATCH, total_minor=110594) is Outcome.FAIL


def test_total_line_sum_tolerance_is_one_per_line() -> None:
    assert (
        outcome(CheckCode.TOTAL_MISMATCH, subtotal_minor=102402, total_minor=110594) is Outcome.PASS
    )
    assert (
        outcome(CheckCode.TOTAL_MISMATCH, subtotal_minor=102403, total_minor=110595) is Outcome.FAIL
    )


def test_total_zero_tax() -> None:
    f = facts(tax_minor=0, total_minor=102400)
    assert run(f)["TOTAL_MISMATCH"].outcome is Outcome.PASS


def test_total_credit_note_with_negative_amounts() -> None:
    f = facts(
        lines=(line(1, "-2", 500, -1000),), subtotal_minor=-1000, tax_minor=-80, total_minor=-1080
    )
    assert run(f)["TOTAL_MISMATCH"].outcome is Outcome.PASS
    assert run(replace(f, total_minor=-1000))["TOTAL_MISMATCH"].outcome is Outcome.FAIL


def test_total_only_does_the_checks_it_has_data_for() -> None:
    no_lines = run(facts(lines=()))["TOTAL_MISMATCH"]
    assert no_lines.outcome is Outcome.PASS and len(no_lines.details["checks"]) == 1  # type: ignore[arg-type]
    assert outcome(CheckCode.TOTAL_MISMATCH, lines=(), tax_minor=None) is Outcome.SKIPPED
    assert outcome(CheckCode.TOTAL_MISMATCH, subtotal_minor=None) is Outcome.SKIPPED


def test_a_matching_lines_sum_alone_never_passes_the_total() -> None:
    # subtotal 102400 matches the lines, but with no tax figure the total (999) was never compared
    r = run(facts(tax_minor=None, total_minor=999))["TOTAL_MISMATCH"]
    assert r.outcome is Outcome.SKIPPED


def test_a_missing_total_is_skipped_even_when_the_lines_add_up() -> None:
    assert outcome(CheckCode.TOTAL_MISMATCH, total_minor=None) is Outcome.SKIPPED


def test_a_failed_lines_sum_still_fails_when_the_total_cannot_be_compared() -> None:
    r = run(facts(subtotal_minor=105000, tax_minor=None))["TOTAL_MISMATCH"]
    assert r.outcome is Outcome.FAIL


def test_total_is_skipped_when_a_line_amount_is_missing() -> None:
    lines = (line(1, "10", 1290, 12900), LineFacts(2, D("10"), 8950, None, None))
    r = run(facts(lines=lines))["TOTAL_MISMATCH"]
    names = [c["name"] for c in r.details["checks"]]  # type: ignore[attr-defined]
    assert names == ["SUBTOTAL_PLUS_TAX_IS_TOTAL"]  # the lines-sum check needs every amount


# ---- tax ---------------------------------------------------------------------------------


def test_tax_of_rounds_half_up_and_is_symmetric_for_credit_notes() -> None:
    assert tax_of(1000, 850) == 85
    assert tax_of(1001, 850) == 85  # 85.085
    assert tax_of(1006, 850) == 86  # 85.51
    assert tax_of(105, 500) == 5  # 5.25
    assert tax_of(-1006, 850) == -86
    assert tax_of(0, 850) == 0


def test_tax_uses_the_suppliers_rate() -> None:
    r = run()["TAX_MISMATCH"]  # 8% of 102400 = 8192
    assert r.outcome is Outcome.PASS and r.details["source"] == "supplier"


def test_tax_fails_against_the_suppliers_rate_with_numbers() -> None:
    r = run(facts(tax_minor=9000, total_minor=111400))["TAX_MISMATCH"]
    assert r.outcome is Outcome.FAIL
    assert (r.details["expected_minor"], r.details["actual_minor"], r.details["rate_bp"]) == (
        8192, 9000, 800,
    )  # fmt: skip


def test_the_suppliers_rate_is_one_rounding_so_the_tolerance_is_one_unit() -> None:
    many = tuple(line(n, "1", 100, 100) for n in range(1, 21))
    f = facts(lines=many, subtotal_minor=2000, tax_minor=160, total_minor=2160)
    assert run(replace(f, tax_minor=161))["TAX_MISMATCH"].outcome is Outcome.PASS
    assert run(replace(f, tax_minor=162))["TAX_MISMATCH"].outcome is Outcome.FAIL  # 20 lines


def test_tax_from_line_rates_tolerates_one_unit_per_line() -> None:
    nobody = facts(
        supplier_name="Nobody Ltd", supplier_tax_id=None,
        lines=(line(1, "10", 1290, 12900, "5"), line(2, "10", 8950, 89500, "5")),
    )  # fmt: skip
    assert run(replace(nobody, tax_minor=5122))["TAX_MISMATCH"].outcome is Outcome.PASS
    assert run(replace(nobody, tax_minor=5123))["TAX_MISMATCH"].outcome is Outcome.FAIL


def test_tax_zero_rate_zero_tax() -> None:
    zero = replace(ACME, tax_rate_bp=0)
    _, results = validate(
        facts(tax_minor=0, total_minor=102400), [zero], ValidationSettings(), TODAY
    )
    assert {r.code: r for r in results}[CheckCode.TAX_MISMATCH].outcome is Outcome.PASS


def test_tax_falls_back_to_the_rates_printed_on_the_lines() -> None:
    unknown = facts(
        supplier_name="Nobody Ltd", supplier_tax_id=None,
        lines=(line(1, "10", 1290, 12900, "5"), line(2, "10", 8950, 89500, "5")),
        tax_minor=5120,
    )  # fmt: skip
    r = run(unknown)["TAX_MISMATCH"]
    assert r.outcome is Outcome.PASS and r.details["source"] == "lines"
    assert run(replace(unknown, tax_minor=8192))["TAX_MISMATCH"].outcome is Outcome.FAIL


def test_tax_is_skipped_without_any_rate_to_compare() -> None:
    nobody = facts(supplier_name="Nobody Ltd", supplier_tax_id=None)
    assert run(nobody)["TAX_MISMATCH"].outcome is Outcome.SKIPPED


def test_tax_is_skipped_without_amounts() -> None:
    assert outcome(CheckCode.TAX_MISMATCH, tax_minor=None) is Outcome.SKIPPED
    assert outcome(CheckCode.TAX_MISMATCH, subtotal_minor=None) is Outcome.SKIPPED


def test_tax_a_known_supplier_without_a_rate_uses_the_lines() -> None:
    no_rate = replace(ACME, tax_rate_bp=None)
    f = facts(lines=(line(1, "10", 1290, 12900, "8"), line(2, "10", 8950, 89500, "8")))
    _, results = validate(f, [no_rate], ValidationSettings(), TODAY)
    assert {r.code: r for r in results}[CheckCode.TAX_MISMATCH].details["source"] == "lines"


# ---- dates -------------------------------------------------------------------------------


def dates(**over: object) -> CheckResult:
    return run(facts(**over))["INVALID_DATE"]


def test_dates_pass_for_a_recent_invoice() -> None:
    assert dates().outcome is Outcome.PASS


def test_a_future_invoice_date_fails() -> None:
    r = dates(invoice_date=date(2031, 6, 22), due_date=date(2031, 7, 22))
    assert r.outcome is Outcome.FAIL
    assert [x["code"] for x in r.details["reasons"]] == ["FUTURE_INVOICE_DATE"]  # type: ignore[attr-defined]


def test_today_is_allowed_and_tomorrow_is_not_by_default() -> None:
    assert dates(invoice_date=TODAY, due_date=TODAY).outcome is Outcome.PASS
    assert (
        dates(invoice_date=date(2026, 9, 26), due_date=date(2026, 10, 26)).outcome is Outcome.FAIL
    )


def test_the_future_tolerance_is_configurable() -> None:
    lenient = ValidationSettings(future_date_tolerance_days=3)
    f = facts(invoice_date=date(2026, 9, 28), due_date=date(2026, 10, 28))
    assert run(f, lenient)["INVALID_DATE"].outcome is Outcome.PASS


def test_an_old_invoice_fails_after_the_maximum_age() -> None:
    assert (
        dates(invoice_date=date(2025, 9, 25), due_date=date(2025, 10, 25)).outcome is Outcome.PASS
    )
    old = dates(invoice_date=date(2025, 9, 24), due_date=date(2025, 10, 24))
    assert old.outcome is Outcome.FAIL
    assert [x["code"] for x in old.details["reasons"]] == ["INVOICE_TOO_OLD"]  # type: ignore[attr-defined]


def test_the_maximum_age_is_configurable() -> None:
    f = facts(invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1))
    assert (
        run(f, ValidationSettings(max_invoice_age_days=30))["INVALID_DATE"].outcome is Outcome.FAIL
    )


def test_a_due_date_before_the_invoice_date_fails() -> None:
    r = dates(invoice_date=date(2026, 5, 27), due_date=date(2026, 5, 15))
    assert r.outcome is Outcome.FAIL
    assert [x["code"] for x in r.details["reasons"]] == ["DUE_BEFORE_INVOICE"]  # type: ignore[attr-defined]


def test_the_same_due_and_invoice_date_is_fine() -> None:
    assert dates(due_date=date(2026, 9, 1)).outcome is Outcome.PASS


def test_a_missing_due_date_is_not_an_error() -> None:
    assert dates(due_date=None).outcome is Outcome.PASS


def test_a_missing_invoice_date_is_skipped() -> None:
    assert dates(invoice_date=None).outcome is Outcome.SKIPPED


def test_several_date_problems_are_all_listed() -> None:
    r = dates(invoice_date=date(2031, 1, 10), due_date=date(2031, 1, 1))
    assert {x["code"] for x in r.details["reasons"]} == {  # type: ignore[attr-defined]
        "FUTURE_INVOICE_DATE", "DUE_BEFORE_INVOICE",
    }  # fmt: skip


# ---- currency ----------------------------------------------------------------------------


def test_currency_matches_the_suppliers_default() -> None:
    assert run()["CURRENCY_MISMATCH"].outcome is Outcome.PASS


def test_currency_that_differs_fails_with_both_currencies() -> None:
    r = run(facts(currency="EUR", printed_currency="EUR"))["CURRENCY_MISMATCH"]
    assert r.outcome is Outcome.FAIL
    assert (r.details["invoice_currency"], r.details["supplier_currency"]) == ("EUR", "USD")


def test_an_unsettled_symbol_is_compared_with_the_suppliers_currency() -> None:
    eur_supplier = replace(ACME, default_currency="EUR")
    _, results = validate(
        facts(currency=None, printed_currency="$"), [eur_supplier], ValidationSettings(), TODAY
    )
    r = {x.code: x for x in results}[CheckCode.CURRENCY_MISMATCH]
    assert r.outcome is Outcome.FAIL and r.details["printed"] == "$"


def test_a_printed_code_that_was_not_supported_is_a_mismatch_too() -> None:
    r = run(facts(currency=None, printed_currency="CHF"))["CURRENCY_MISMATCH"]
    assert r.outcome is Outcome.FAIL


def test_currency_is_skipped_when_nothing_was_printed() -> None:
    assert (
        outcome(CheckCode.CURRENCY_MISMATCH, currency=None, printed_currency=None)
        is Outcome.SKIPPED
    )


def test_currency_is_skipped_for_unreadable_junk() -> None:
    assert (
        outcome(CheckCode.CURRENCY_MISMATCH, currency=None, printed_currency="???")
        is Outcome.SKIPPED
    )


def test_currency_is_skipped_for_an_unknown_supplier() -> None:
    nobody = facts(supplier_name="Nobody Ltd", supplier_tax_id=None)
    assert run(nobody)["CURRENCY_MISMATCH"].outcome is Outcome.SKIPPED


# ---- supplier ----------------------------------------------------------------------------


def test_a_supplier_is_known_by_an_exact_tax_id() -> None:
    m = match_supplier(None, " 69-9397618 ", SUPPLIERS, 90)
    assert m.supplier == ACME and m.matched_by == "tax_id"


def test_a_tax_id_and_an_agreeing_name_are_known() -> None:
    m = match_supplier("Acme Supplies Ltd.", "69-9397618", SUPPLIERS, 90)
    assert m.supplier == ACME and m.conflict is None


def test_a_tax_id_with_a_name_that_is_not_the_suppliers_is_a_conflict() -> None:
    m = match_supplier("Something Else", "69-9397618", SUPPLIERS, 90)
    assert m.supplier is None and m.conflict == "NAME_DIFFERS_FROM_TAX_ID"
    assert m.closest_name == "Acme Supplies Ltd."


def test_a_tax_id_of_one_supplier_with_the_name_of_another_is_a_conflict() -> None:
    m = match_supplier("Coastal Packaging Ltd.", "69-9397618", SUPPLIERS, 90)
    assert m.supplier is None and m.conflict == "NAME_DIFFERS_FROM_TAX_ID"


def test_a_supplier_is_known_by_an_exact_name_or_alias() -> None:
    assert match_supplier("ACME  supplies, ltd", None, SUPPLIERS, 90).supplier == ACME
    assert match_supplier("Coastal Pack", None, SUPPLIERS, 90).supplier == COASTAL
    assert match_supplier("Coastal Pack", None, SUPPLIERS, 90).matched_by == "name"


def test_punctuation_and_case_do_not_matter_for_an_exact_name() -> None:
    assert match_supplier("acme supplies ltd", None, SUPPLIERS, 90).supplier == ACME


def test_a_near_miss_name_is_not_known_but_is_suggested() -> None:
    # 95% similar to a real supplier, and no tax id: a classic impersonation pattern
    m = match_supplier("Coastal Packing Ltd.", None, SUPPLIERS, 90)
    assert m.supplier is None and m.matched_by is None
    assert m.closest_name == "Coastal Packaging Ltd." and m.closest_score >= 90


def test_a_weak_similarity_is_reported_but_not_suggested() -> None:
    m = match_supplier("Zenith Novelty Traders", None, SUPPLIERS, 90)
    assert m.supplier is None and m.closest_score < 90


def test_a_known_name_with_a_different_tax_id_is_a_conflict_not_a_match() -> None:
    m = match_supplier("Acme Supplies Ltd.", "00-0000000", SUPPLIERS, 90)
    assert m.supplier is None and m.conflict == "TAX_ID_DIFFERS_FROM_RECORD"
    assert m.closest_name == "Acme Supplies Ltd."


def test_a_known_name_is_enough_when_no_tax_id_is_printed_or_on_file() -> None:
    assert match_supplier("Acme Supplies Ltd.", None, SUPPLIERS, 90).supplier == ACME
    no_tax = replace(ACME, tax_id=None)
    assert match_supplier("Acme Supplies Ltd.", "00-0000000", [no_tax], 90).supplier == no_tax


def test_punctuation_only_names_match_nobody() -> None:
    blank = replace(ACME, name="---", aliases=())
    assert match_supplier("***", None, [blank], 90).supplier is None


def test_no_name_and_no_tax_id_matches_nobody() -> None:
    m = match_supplier(None, None, SUPPLIERS, 90)
    assert m.supplier is None and m.closest_name is None


def test_an_empty_supplier_list_matches_nobody() -> None:
    assert match_supplier("Acme", None, [], 90).supplier is None


def test_unknown_supplier_check_reports_the_closest_match() -> None:
    f = facts(supplier_name="Coastal Packing Ltd.", supplier_tax_id=None)
    r = run(f)["UNKNOWN_SUPPLIER"]
    assert r.outcome is Outcome.FAIL
    assert r.details["closest_name"] == "Coastal Packaging Ltd."
    assert r.details["suggested"] is True and r.details["printed_name"] == "Coastal Packing Ltd."


def test_unknown_supplier_reports_a_conflict() -> None:
    r = run(facts(supplier_tax_id="00-0000000"))["UNKNOWN_SUPPLIER"]
    assert r.outcome is Outcome.FAIL and r.details["conflict"] == "TAX_ID_DIFFERS_FROM_RECORD"


def test_unknown_supplier_passes_for_a_known_one() -> None:
    r = run()["UNKNOWN_SUPPLIER"]
    assert r.outcome is Outcome.PASS and r.details["supplier_id"] == "s1"


def test_unknown_supplier_is_skipped_with_nothing_to_identify_it_by() -> None:
    assert (
        outcome(CheckCode.UNKNOWN_SUPPLIER, supplier_name=None, supplier_tax_id=None)
        is Outcome.SKIPPED
    )


# ---- bank --------------------------------------------------------------------------------


def test_the_same_bank_account_passes() -> None:
    assert run()["BANK_DETAILS_CHANGED"].outcome is Outcome.PASS


def test_a_different_bank_account_fails() -> None:
    r = run(facts(bank_account_hash="H-OTHER"))["BANK_DETAILS_CHANGED"]
    assert r.outcome is Outcome.FAIL and r.details["supplier_id"] == "s1"


def test_bank_hashes_are_never_put_in_the_details() -> None:
    r = run(facts(bank_account_hash="H-OTHER"))["BANK_DETAILS_CHANGED"]
    assert "H-OTHER" not in str(r.details) and "H-ACME" not in str(r.details)


def test_a_bank_account_certainly_absent_from_the_invoice_passes() -> None:
    r = run(facts(bank_account_hash=None, bank_absence_certain=True))["BANK_DETAILS_CHANGED"]
    assert r.outcome is Outcome.PASS and r.details["bank_on_invoice"] is False


def test_a_bank_account_that_may_just_be_unread_is_skipped_not_passed() -> None:
    r = run(facts(bank_account_hash=None, bank_absence_certain=False))["BANK_DETAILS_CHANGED"]
    assert r.outcome is Outcome.SKIPPED and r.details["reason"] == "BANK_NOT_READ"


def test_a_supplier_without_a_bank_on_file_cannot_be_compared() -> None:
    no_bank = replace(ACME, bank_account_hash=None)
    _, results = validate(facts(), [no_bank], ValidationSettings(), TODAY)
    assert {r.code: r for r in results}[CheckCode.BANK_DETAILS_CHANGED].outcome is Outcome.SKIPPED


def test_bank_is_skipped_for_an_unknown_supplier() -> None:
    nobody = facts(supplier_name="Nobody Ltd", supplier_tax_id=None, bank_account_hash="H-OTHER")
    assert run(nobody)["BANK_DETAILS_CHANGED"].outcome is Outcome.SKIPPED


# ---- settings ----------------------------------------------------------------------------


def test_settings_defaults_match_the_agreed_values() -> None:
    s = ValidationSettings()
    assert (s.max_invoice_age_days, s.future_date_tolerance_days, s.supplier_fuzzy_min) == (
        365,
        0,
        90,
    )
    assert (s.line_tolerance_minor, s.total_tolerance_minor) == (1, 1)


def test_settings_are_read_from_the_tenant_with_defaults_for_the_rest() -> None:
    s = ValidationSettings.from_tenant({"max_invoice_age_days": 90, "price_tolerance_pct": 2.0})
    assert s.max_invoice_age_days == 90 and s.supplier_fuzzy_min == 90


BAD_SETTINGS = [
    {"max_invoice_age_days": -1},
    {"max_invoice_age_days": "soon"},
    {"supplier_fuzzy_min": 101},
    {"future_date_tolerance_days": True},
]


@pytest.mark.parametrize("bad", BAD_SETTINGS)
def test_bad_tenant_settings_fall_back_to_the_safe_default(bad: dict[str, object]) -> None:
    assert ValidationSettings.from_tenant(bad) == ValidationSettings()


def test_untrusted_text_in_the_details_is_clipped() -> None:
    long_name = "X" * 5000
    r = run(facts(supplier_name=long_name, supplier_tax_id=None))["UNKNOWN_SUPPLIER"]
    assert len(str(r.details["printed_name"])) <= 100
    junk = run(facts(currency=None, printed_currency="C" * 500))["CURRENCY_MISMATCH"]
    assert len(str(junk.details["printed"])) <= 100


def test_currency_codes_compare_case_insensitively() -> None:
    assert outcome(CheckCode.CURRENCY_MISMATCH, currency="usd") is Outcome.PASS
