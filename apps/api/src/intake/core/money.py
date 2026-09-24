"""Money as integer minor units plus an ISO 4217 currency code. Never floats."""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from intake.core.numbers import parse_number

# Minor-unit exponents for the currencies we support. Unknown currencies are rejected
# rather than guessed (uncertain means human).
CURRENCY_EXPONENT: dict[str, int] = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}

# Symbols and the one currency each stands for. `$` and `¥` are also used by other currencies, so
# `normalize_currency` treats them as ambiguous; here we only require the symbol to match the
# currency the caller already settled on.
_SYMBOL_CODE = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}


def _strip_currency_code(raw: str, currency: str) -> str:
    """Drop a printed code that matches `currency` ('7,00 EUR'); a different code is an error."""
    m = re.match(r"^(?:([A-Za-z]{3})\s*)?(.*?)(?:\s*([A-Za-z]{3}))?$", raw.strip(), re.DOTALL)
    if m is None:  # pragma: no cover - the pattern matches any string
        return raw
    for code in (m[1], m[3]):
        if code and code.upper() != currency:
            raise ValueError(f"amount is in {code.upper()}, expected {currency}: {raw!r}")
    return m[2]


def exponent(currency: str) -> int:
    try:
        return CURRENCY_EXPONENT[currency]
    except KeyError:
        raise ValueError(f"unsupported currency: {currency!r}") from None


@dataclass(frozen=True)
class Money:
    minor: int
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.minor, bool) or not isinstance(self.minor, int):
            raise TypeError("Money.minor must be an int (minor units), never a float")
        exponent(self.currency)

    def __add__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.minor - other.minor, self.currency)

    def _same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")


def _strip_symbols(raw: str, currency: str) -> str:
    """Remove currency symbols, refusing one that belongs to a different currency."""
    for symbol, code in _SYMBOL_CODE.items():
        if symbol in raw:
            if code != currency:
                raise ValueError(f"amount uses {symbol} but the currency is {currency}")
            raw = raw.replace(symbol, "")
    return raw.strip()


def parse_money(text: str, currency: str) -> Money:
    """Parse a printed amount like '$1,234.50' or '1.234,50 EUR' into Money.

    Raises ValueError for anything ambiguous, misgrouped, in another currency, or with more
    precision than the currency allows.
    """
    exp = exponent(currency)
    raw = text.strip()
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1]
    raw = _strip_symbols(_strip_currency_code(raw, currency), currency)
    value = parse_number(raw, max_decimals=exp)
    minor = int(value.scaleb(exp))  # exact: parse_number allowed at most `exp` decimals
    return Money(-abs(minor) if negative else minor, currency)


def format_money(money: Money) -> str:
    exp = exponent(money.currency)
    sign = "-" if money.minor < 0 else ""
    digits = abs(money.minor)
    if exp == 0:
        return f"{money.currency} {sign}{digits:,}"
    whole, frac = divmod(digits, 10**exp)
    return f"{money.currency} {sign}{whole:,}.{frac:0{exp}d}"


def line_amount_minor(quantity: Decimal, unit_price_minor: int) -> int:
    """quantity x unit price in minor units, rounded half up to a whole minor unit."""
    return int((quantity * unit_price_minor).quantize(Decimal(1), rounding=ROUND_HALF_UP))
