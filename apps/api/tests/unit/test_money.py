from decimal import Decimal

import pytest

from intake.core.money import Money, format_money, line_amount_minor, parse_money


def test_parse_simple_decimal() -> None:
    assert parse_money("45.00", "USD") == Money(4500, "USD")


def test_parse_thousands_separators_and_symbol() -> None:
    assert parse_money("$1,234.50", "USD") == Money(123450, "USD")


def test_parse_european_format() -> None:
    assert parse_money("1.234,50", "EUR") == Money(123450, "EUR")


def test_parse_zero_decimal_currency() -> None:
    assert parse_money("1500", "JPY") == Money(1500, "JPY")


def test_parse_negative_in_parentheses() -> None:
    assert parse_money("(12.00)", "USD") == Money(-1200, "USD")


@pytest.mark.parametrize("bad", ["", "abc", "1.2.3.4x", "12.345"])
def test_parse_rejects_garbage_or_excess_precision(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_money(bad, "USD")


def test_money_addition_same_currency() -> None:
    assert Money(100, "USD") + Money(250, "USD") == Money(350, "USD")


def test_money_addition_mixed_currency_raises() -> None:
    with pytest.raises(ValueError):
        Money(100, "USD") + Money(100, "EUR")


def test_money_rejects_float_minor() -> None:
    with pytest.raises(TypeError):
        Money(1.5, "USD")  # type: ignore[arg-type]


def test_format_money() -> None:
    assert format_money(Money(214000, "USD")) == "USD 2,140.00"
    assert format_money(Money(1500, "JPY")) == "JPY 1,500"
    assert format_money(Money(-1200, "USD")) == "USD -12.00"


def test_line_amount_exact() -> None:
    assert line_amount_minor(Decimal("12"), 4500) == 54000


def test_line_amount_rounds_half_up() -> None:
    assert line_amount_minor(Decimal("0.5"), 5) == 3  # 2.5 -> 3
    assert line_amount_minor(Decimal("1.5"), 333) == 500  # 499.5 -> 500


def test_unsupported_currency_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported currency"):
        Money(100, "XXX")
    with pytest.raises(ValueError):
        parse_money("1.00", "ZZZ")


def test_money_subtraction() -> None:
    assert Money(500, "USD") - Money(200, "USD") == Money(300, "USD")
    with pytest.raises(ValueError):
        Money(500, "USD") - Money(200, "GBP")


def test_parse_same_amount_in_both_number_formats() -> None:
    assert parse_money("1.234,56", "EUR") == parse_money("1,234.56", "EUR") == Money(123456, "EUR")


def test_parse_comma_decimal_without_thousands() -> None:
    assert parse_money("12,50", "EUR") == Money(1250, "EUR")


def test_parse_space_thousands_separator() -> None:
    assert parse_money("1 234,56", "EUR") == Money(123456, "EUR")


def test_parse_leading_minus_for_credit_notes() -> None:
    assert parse_money("-45.00", "USD") == Money(-4500, "USD")


def test_parse_single_dot_with_three_digits_is_rejected_not_guessed() -> None:
    # "1.234" could be 1234 (thousands) or 1.234 (too precise): uncertain means human.
    with pytest.raises(ValueError):
        parse_money("1.234", "EUR")


def test_parse_currency_symbol_after_amount() -> None:
    assert parse_money("1.234,56 €", "EUR") == Money(123456, "EUR")
