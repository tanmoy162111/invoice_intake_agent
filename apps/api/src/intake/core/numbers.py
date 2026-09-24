"""One strict parser for printed numbers, shared by money, quantities and tax rates.

Decimal and thousands marks differ by country ('1,234.56' vs '1.234,56'), so a guess here quietly
turns 1.200 kg into 1,200 kg. Every shape is validated; anything a person could read two ways raises
`AmbiguousNumberError` and the value goes to review instead of being guessed.
"""

import re
from decimal import Decimal

_SPACES = re.compile(r"[   ]")
_SPACE_GROUPED = re.compile(r"([1-9]\d{0,2})((?: \d{3})+)([.,]\d+)?")
_WHOLE = re.compile(r"0|[1-9]\d*")
_THOUSANDS_HEAD = re.compile(r"[1-9]\d{0,2}")


class AmbiguousNumberError(ValueError):
    """The text has more than one sensible reading (for example 1.200)."""


def _grouped(whole: str, sep: str) -> str:
    """Digits of a whole part written with `sep` as thousands mark, or ValueError."""
    if not re.fullmatch(rf"[1-9]\d{{0,2}}(?:{re.escape(sep)}\d{{3}})+", whole):
        raise ValueError(f"badly grouped number: {whole!r}")
    return whole.replace(sep, "")


def parse_number(text: str, *, max_decimals: int | None) -> Decimal:
    """Parse '1,234.56', '1.234,56', '1 234,56', '12,5', '0,500'... `max_decimals` is how many
    decimal places are possible (a currency's exponent), or None when unlimited (quantities)."""
    s = _SPACES.sub(" ", text.strip())
    negative = s.startswith("-")
    s = s.removeprefix("-")
    if not s or not re.fullmatch(r"[\d.,  ]+", s):
        raise ValueError(f"not a number: {text!r}")

    if " " in s:  # a space is only ever a thousands mark, and needs 3-digit groups
        m = _SPACE_GROUPED.fullmatch(s)
        if m is None:
            raise ValueError(f"badly grouped number: {text!r}")
        s = m[1] + m[2].replace(" ", "") + (m[3] or "")

    whole, frac = _split(s, max_decimals, text)
    if len(frac) > (max_decimals if max_decimals is not None else len(frac)):
        raise ValueError(f"too much precision: {text!r}")
    value = Decimal(f"{whole}.{frac}" if frac else whole)
    return -value if negative else value


def _split(s: str, max_decimals: int | None, original: str) -> tuple[str, str]:
    """(whole digits, decimal digits) for a string of digits, '.' and ','."""
    if s.isdigit():
        return s, ""
    dots, commas = s.count("."), s.count(",")
    if dots and commas:  # the last mark is the decimal mark, the other groups thousands
        pos = max(s.rfind("."), s.rfind(","))
        dec_sep, group_sep = s[pos], "," if s[pos] == "." else "."
        head, frac = s[:pos], s[pos + 1 :]
        if not frac.isdigit() or dec_sep in head:
            raise ValueError(f"not a number: {original!r}")
        return _grouped(head, group_sep), frac
    sep = "." if dots else ","
    if s.count(sep) > 1:  # repeated: a thousands mark, no decimals
        return _grouped(s, sep), ""
    head, _, frac = s.partition(sep)
    if not head.isdigit() or not frac.isdigit():
        raise ValueError(f"not a number: {original!r}")
    if len(frac) == 3 and _THOUSANDS_HEAD.fullmatch(head):
        # 1,234 or 1.234: thousands or decimals? Only a currency with < 3 decimals settles it, and
        # only for a comma (the dot is the EU thousands mark or the US decimal mark).
        if sep == "," and max_decimals is not None and max_decimals < 3:
            return head + frac, ""
        raise AmbiguousNumberError(f"could be a thousands mark or a decimal mark: {original!r}")
    if not _WHOLE.fullmatch(head):
        raise ValueError(f"leading zero: {original!r}")
    return head, frac
