"""Pure normalizers used before comparing values."""

import re

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


def normalize_bank_account(value: str) -> str:
    """Canonical form of an IBAN / account number: alphanumerics only, upper case."""
    return _NON_ALNUM.sub("", value).upper()
