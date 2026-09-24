from datetime import date
from decimal import Decimal

import pytest

from intake.core.normalize import (
    AmbiguousCurrencyError,
    AmbiguousDateError,
    normalize_bank_account,
    normalize_currency,
    normalize_supplier_name,
    parse_date,
    parse_quantity,
    parse_tax_rate,
)
from intake.core.numbers import AmbiguousNumberError


def test_bank_account_strips_spaces_and_dashes_and_uppercases() -> None:
    assert normalize_bank_account("gb29 nwbk-6016 1331 9268 19") == "GB29NWBK60161331926819"


def test_bank_account_empty() -> None:
    assert normalize_bank_account(" - ") == ""


@pytest.mark.parametrize(
    "text",
    ["2026-03-13", "13/03/2026", "13.03.2026", "03/13/2026", "March 13, 2026", "13 Mar 2026"],
)
def test_parse_date_unambiguous_formats(text: str) -> None:
    assert parse_date(text) == date(2026, 3, 13)


def test_parse_date_ambiguous_slash_raises_instead_of_guessing() -> None:
    with pytest.raises(AmbiguousDateError):
        parse_date("03/04/2026")


def test_parse_date_ambiguous_resolved_by_hint() -> None:
    assert parse_date("03/04/2026", day_first=True) == date(2026, 4, 3)
    assert parse_date("03/04/2026", day_first=False) == date(2026, 3, 4)


def test_dotted_numeric_dates_are_day_first() -> None:
    # 07.06.2026 is 7 June: a dot separator is the day-first (European) convention.
    assert parse_date("07.06.2026") == date(2026, 6, 7)
    assert parse_date("03.04.2026") == date(2026, 4, 3)


def test_dotted_date_with_an_impossible_month_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_date("03.13.2026")


def test_hyphenated_numeric_dates_stay_ambiguous() -> None:
    with pytest.raises(AmbiguousDateError):
        parse_date("03-04-2026")


def test_parse_date_same_day_and_month_is_not_ambiguous() -> None:
    assert parse_date("05/05/2026") == date(2026, 5, 5)


@pytest.mark.parametrize(
    "bad", ["", "not a date", "31/02/2026", "2026-13-01", "13/13/2026", "13 Foo 2026"]
)
def test_parse_date_rejects_garbage_and_impossible_dates(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_date(bad)


def test_parse_date_two_digit_year_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_date("13/03/26")


@pytest.mark.parametrize(
    ("text", "code"),
    [("USD", "USD"), (" eur ", "EUR"), ("€", "EUR"), ("£", "GBP"), ("JPY", "JPY")],
)
def test_normalize_currency(text: str, code: str) -> None:
    assert normalize_currency(text) == code


@pytest.mark.parametrize("bad", ["", "XXX", "dollars", "US"])
def test_normalize_currency_rejects_unknown(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_currency(bad)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12", "12"),
        ("12.5", "12.5"),
        ("1.200,5", "1200.5"),
        ("1,200.5", "1200.5"),
        ("1,5", "1.5"),
        ("0,500", "0.5"),
        ("12,345.6", "12345.6"),
        ("0.25", "0.25"),
        (" 3 ", "3"),
    ],
)
def test_parse_quantity(text: str, expected: str) -> None:
    assert parse_quantity(text) == Decimal(expected)


@pytest.mark.parametrize("bad", ["", "abc", "1.2.3x", "-", "1e3"])
def test_parse_quantity_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_quantity(bad)


def test_parse_quantity_negative_for_credit_notes() -> None:
    assert parse_quantity("-2") == Decimal("-2")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("19%", "19"), ("19.00 %", "19"), ("7,5%", "7.5"), ("0%", "0"), ("20", "20")],
)
def test_parse_tax_rate_is_percent(text: str, expected: str) -> None:
    assert parse_tax_rate(text) == Decimal(expected)


@pytest.mark.parametrize("bad", ["", "abc", "-5%", "150%"])
def test_parse_tax_rate_rejects_out_of_range_or_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_tax_rate(bad)


def test_supplier_name_trimmed_casefolded_whitespace_collapsed() -> None:
    assert normalize_supplier_name("  Rheinwerk   Bürobedarf\tGmbH ") == "rheinwerk bürobedarf gmbh"


def test_supplier_name_unicode_forms_compare_equal() -> None:
    assert normalize_supplier_name("Büro AG") == normalize_supplier_name("Büro AG")


def test_supplier_name_casefold_handles_sharp_s() -> None:
    assert normalize_supplier_name("STRASSE Ltd") == normalize_supplier_name("Straße Ltd")


def test_dollar_and_yen_symbols_are_ambiguous_without_a_hint() -> None:
    for symbol in ("$", "¥"):
        with pytest.raises(AmbiguousCurrencyError):
            normalize_currency(symbol)


def test_a_suppliers_default_currency_can_settle_a_symbol() -> None:
    assert normalize_currency("$", hint="USD") == "USD"
    assert normalize_currency("¥", hint="JPY") == "JPY"
    for bad_hint in ("EUR", "GBP", None):
        with pytest.raises(AmbiguousCurrencyError):
            normalize_currency("$", hint=bad_hint)


def test_a_hint_never_overrides_a_printed_code() -> None:
    assert normalize_currency("EUR", hint="USD") == "EUR"


@pytest.mark.parametrize("text", ["1.200", "1,200", "12.345"])
def test_quantities_with_a_lone_separator_before_three_digits_are_ambiguous(text: str) -> None:
    with pytest.raises(AmbiguousNumberError):
        parse_quantity(text)


@pytest.mark.parametrize("text", ["0,075", "0.5", ".5"])
def test_a_tax_rate_below_one_without_a_percent_sign_is_ambiguous(text: str) -> None:
    with pytest.raises(ValueError):
        parse_tax_rate(text)


def test_a_tax_rate_below_one_with_a_percent_sign_is_a_percentage() -> None:
    assert parse_tax_rate("0,075%") == Decimal("0.075")
    assert parse_tax_rate("0%") == Decimal("0")
    assert parse_tax_rate("0") == Decimal("0")
