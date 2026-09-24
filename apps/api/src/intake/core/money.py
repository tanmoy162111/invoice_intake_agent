"""Money as integer minor units plus an ISO 4217 currency code. Never floats."""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Minor-unit exponents for the currencies we support. Unknown currencies are rejected
# rather than guessed (uncertain means human).
CURRENCY_EXPONENT: dict[str, int] = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}

_SYMBOLS = "$€£¥  "
_NUMBER = re.compile(r"^-?\d+(?:[.,]\d+)*$")


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


def parse_money(text: str, currency: str) -> Money:
    """Parse a printed amount like '$1,234.50' or '1.234,50' into Money.

    Raises ValueError for anything ambiguous or with more precision than the currency allows.
    """
    exp = exponent(currency)
    raw = text.strip()
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1]
    raw = "".join(ch for ch in raw if ch not in _SYMBOLS)
    if not raw or not _NUMBER.match(raw):
        raise ValueError(f"not an amount: {text!r}")
    sign = -1 if raw.startswith("-") or negative else 1
    raw = raw.lstrip("-")

    last_dot, last_comma = raw.rfind("."), raw.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        dec_pos = max(last_dot, last_comma)
    elif last_dot >= 0 or last_comma >= 0:
        sep = "." if last_dot >= 0 else ","
        pos = raw.rfind(sep)
        digits_after = len(raw) - pos - 1
        # A repeated separator, or a comma followed by exactly 3 digits, is a thousands mark.
        is_thousands = raw.count(sep) > 1 or (sep == "," and digits_after == 3)
        dec_pos = -1 if is_thousands else pos
    else:
        dec_pos = -1

    if dec_pos >= 0:
        whole, frac = raw[:dec_pos], raw[dec_pos + 1 :]
    else:
        whole, frac = raw, ""
    whole = whole.replace(".", "").replace(",", "")
    if len(frac) > exp:
        raise ValueError(f"too much precision for {currency}: {text!r}")
    minor = int(whole or "0") * 10**exp + int(frac.ljust(exp, "0") or "0")
    return Money(sign * minor, currency)


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
