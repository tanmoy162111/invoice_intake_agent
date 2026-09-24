from decimal import Decimal

import pytest

from intake.core.numbers import AmbiguousNumberError, parse_number

D = Decimal


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12", "12"),
        ("0", "0"),
        ("12.5", "12.5"),
        ("12,5", "12.5"),
        ("0.5", "0.5"),
        ("1,234.56", "1234.56"),
        ("1.234,56", "1234.56"),
        ("12,345.6", "12345.6"),
        ("1,234,567", "1234567"),
        ("1.234.567", "1234567"),
        ("1,234,567.89", "1234567.89"),
        ("1 234,56", "1234.56"),
        ("1 234,56", "1234.56"),
        ("1 234.56", "1234.56"),
        ("12 345", "12345"),
        ("-12.5", "-12.5"),
    ],
)
def test_valid_numbers(text: str, expected: str) -> None:
    assert parse_number(text, max_decimals=None) == D(expected)


@pytest.mark.parametrize(
    "text",
    [
        "1.234.56",  # a dot both groups and marks decimals
        "1,23.45",  # a group of two digits
        "12,34,567",  # Indian-style grouping is not supported: refuse, don't guess
        "1,234,56",
        "1.2.3",
        "1 23 456",
        "1 234 5",
        "1,,234",
        "",
        "  ",
        "abc",
        "1e3",
        "--1",
        "1,",
        ",5",
        "1..5",
        "1,234.567.8",
        "01,234",  # a group cannot start with 0
        "1 234,56 78",
    ],
)
def test_malformed_numbers_are_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse_number(text, max_decimals=None)


@pytest.mark.parametrize("text", ["1.200", "1,200", "12.345", "999,999"])
def test_a_single_separator_before_exactly_three_digits_is_ambiguous_for_quantities(
    text: str,
) -> None:
    with pytest.raises(AmbiguousNumberError):
        parse_number(text, max_decimals=None)


@pytest.mark.parametrize(
    ("text", "expected"), [("0,500", "0.500"), ("0.500", "0.500"), ("0,075", "0.075")]
)
def test_a_leading_zero_makes_the_separator_a_decimal_mark(text: str, expected: str) -> None:
    # a thousands group can never start with 0, so 0,500 is one half
    assert parse_number(text, max_decimals=None) == D(expected)


def test_money_reads_comma_before_three_digits_as_thousands() -> None:
    # for a currency with at most 2 decimals, 1,234 can only be one thousand two hundred thirty-four
    assert parse_number("1,234", max_decimals=2) == D("1234")
    assert parse_number("12,345", max_decimals=0) == D("12345")


def test_money_refuses_dot_before_three_digits() -> None:
    # 1.234 is too precise as a decimal and only an EU-style thousands mark otherwise: ask a person
    with pytest.raises(AmbiguousNumberError):
        parse_number("1.234", max_decimals=2)


def test_precision_beyond_the_currency_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_number("12.345", max_decimals=2)
    with pytest.raises(ValueError):
        parse_number("0,500", max_decimals=2)
    with pytest.raises(ValueError):
        parse_number("1.5", max_decimals=0)
    assert parse_number("12.30", max_decimals=2) == D("12.30")


def test_ambiguous_number_error_is_a_value_error() -> None:
    assert issubclass(AmbiguousNumberError, ValueError)
