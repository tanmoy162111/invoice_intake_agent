"""Pure normalizers for extracted values (dates, currency, quantities, names).

Anything ambiguous raises rather than guessing: uncertain means human.
"""

import re
import unicodedata
from datetime import date
from decimal import Decimal

from intake.core.money import CURRENCY_EXPONENT

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


def normalize_bank_account(value: str) -> str:
    """Canonical form of an IBAN / account number: alphanumerics only, upper case."""
    return _NON_ALNUM.sub("", value).upper()


class AmbiguousDateError(ValueError):
    """The text could be day-first or month-first and no hint was given."""


_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}  # fmt: skip
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_NUMERIC = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_DAY_MONTH_NAME = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[ \-]([A-Za-z]+)\.?,?[ \-](\d{4})$")
_MONTH_NAME_DAY = re.compile(r"^([A-Za-z]+)\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})$")


def _build(year: int, month: int, day: int, text: str) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        raise ValueError(f"not a real date: {text!r}") from None


def parse_date(text: str, *, day_first: bool | None = None) -> date:
    """Parse a printed date. Two-digit years and ambiguous d/m vs m/d raise."""
    raw = text.strip()
    if m := _ISO.match(raw):
        return _build(int(m[1]), int(m[2]), int(m[3]), text)
    if m := _NUMERIC.match(raw):
        a, b, year = int(m[1]), int(m[2]), int(m[3])
        if a > 12:
            return _build(year, b, a, text)
        if b > 12:
            return _build(year, a, b, text)
        if a == b:
            return _build(year, a, b, text)
        if day_first is None:
            raise AmbiguousDateError(f"day-first or month-first? {text!r}")
        return _build(year, b, a, text) if day_first else _build(year, a, b, text)
    if m := _DAY_MONTH_NAME.match(raw):
        day, name, year = int(m[1]), m[2].lower(), int(m[3])
    elif m := _MONTH_NAME_DAY.match(raw):
        name, day, year = m[1].lower(), int(m[2]), int(m[3])
    else:
        raise ValueError(f"not a date: {text!r}")
    if name not in _MONTHS:
        raise ValueError(f"unknown month: {text!r}")
    return _build(year, _MONTHS[name], day, text)


_CURRENCY_SYMBOLS = {"€": "EUR", "£": "GBP", "$": "USD", "¥": "JPY"}


def normalize_currency(text: str) -> str:
    """ISO 4217 code from a code or common symbol. Unsupported currencies raise."""
    raw = text.strip()
    code = _CURRENCY_SYMBOLS.get(raw, raw.upper())
    if code not in CURRENCY_EXPONENT:
        raise ValueError(f"unsupported currency: {text!r}")
    return code


_DECIMAL_TEXT = re.compile(r"^-?\d+(?:[.,]\d+)*$")


def parse_quantity(text: str) -> Decimal:
    """Parse a quantity in either number format ('1,200', '1.200,5', '1,5')."""
    raw = text.strip().replace(" ", "")
    if not _DECIMAL_TEXT.match(raw):
        raise ValueError(f"not a quantity: {text!r}")
    negative = raw.startswith("-")
    raw = raw.lstrip("-")
    last_dot, last_comma = raw.rfind("."), raw.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        dec_pos = max(last_dot, last_comma)
    else:
        sep = "." if last_dot >= 0 else ","
        pos = raw.rfind(sep)
        thousands = pos >= 0 and (raw.count(sep) > 1 or (sep == "," and len(raw) - pos - 1 == 3))
        dec_pos = -1 if pos < 0 or thousands else pos
    whole, frac = (raw[:dec_pos], raw[dec_pos + 1 :]) if dec_pos >= 0 else (raw, "")
    digits = whole.replace(".", "").replace(",", "")
    value = Decimal(f"{digits}.{frac}" if frac else digits)
    return -value if negative else value


def parse_tax_rate(text: str) -> Decimal:
    """A tax rate as a percentage (19 means 19%). Outside 0-100 raises."""
    raw = text.strip().removesuffix("%").strip()
    value = parse_quantity(raw)
    if not Decimal(0) <= value <= Decimal(100):
        raise ValueError(f"tax rate out of range: {text!r}")
    return value


def normalize_supplier_name(text: str) -> str:
    """Trimmed, NFKC-normalized, case-folded, whitespace-collapsed form for matching."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())
