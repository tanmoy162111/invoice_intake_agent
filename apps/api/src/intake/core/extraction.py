"""Turn the model's raw answer into normalized values and per-field confidence (playbook §6.3).

Pure: the caller supplies the page texts and what master data it found. Nothing here guesses:
a value that can't be normalized unambiguously becomes null with zero confidence, so it goes to
a person. Bank accounts are normalized here but never encrypted or stored here.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from intake.core.confidence import (
    SelfConfidence,
    Signals,
    amount_in_text,
    currency_agreement,
    date_in_text,
    number_in_text,
    percent_in_text,
    score_field,
    sum_supports,
    text_in_text,
)
from intake.core.money import Money, exponent, line_amount_minor, parse_money
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
from intake.core.statuses import DocQuality

_ZERO = Decimal(0)
_MONEY_FIELDS = frozenset({"subtotal", "tax_total", "total"})
_DATE_FIELDS = frozenset({"invoice_date", "due_date"})


class NormalizeError(StrEnum):
    """Why a value was left empty (stored in `signals["normalize_error"]`; see the manual)."""

    AMBIGUOUS_DATE = "AMBIGUOUS_DATE"
    AMBIGUOUS_NUMBER = "AMBIGUOUS_NUMBER"
    AMBIGUOUS_CURRENCY = "AMBIGUOUS_CURRENCY"
    UNSUPPORTED_CURRENCY = "UNSUPPORTED_CURRENCY"
    NO_CURRENCY = "NO_CURRENCY"
    UNPARSEABLE = "UNPARSEABLE"


def line_field_name(line_no: int, name: str) -> str:
    return f"line.{line_no}.{name}"


@dataclass(frozen=True)
class RawField:
    value: str | None
    self_confidence: SelfConfidence
    page: int | None


@dataclass(frozen=True)
class RawInvoice:
    header: Mapping[str, RawField]
    lines: Sequence[Mapping[str, RawField]]


@dataclass(frozen=True)
class MasterData:
    """What the caller found in supplier master data. Absence is not a contradiction here."""

    tax_id_known: bool = False
    name_known: bool = False
    default_currency: str | None = None  # the matched supplier's usual currency


@dataclass(frozen=True)
class FieldResult:
    field: str
    raw: str | None
    normalized: str | None
    confidence: Decimal
    signals: dict[str, object]
    page: int | None


@dataclass(frozen=True)
class LineResult:
    line_no: int
    description: str | None
    sku: str | None
    quantity: Decimal | None
    unit_price_minor: int | None
    amount_minor: int | None
    tax_rate: Decimal | None


@dataclass(frozen=True)
class Interpreted:
    supplier_name: str | None
    invoice_number: str | None
    po_number: str | None
    payment_terms: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str | None
    subtotal_minor: int | None
    tax_minor: int | None
    total_minor: int | None
    lines: list[LineResult]
    fields: list[FieldResult] = field(default_factory=list)


@dataclass(frozen=True)
class _Parsed:
    """One normalized value. `typed` is what goes in the invoice column; `text` is the string
    form stored beside the raw value. `error` is set when it could not be normalized."""

    typed: object | None = None
    text: str | None = None
    error: NormalizeError | None = None
    weak: bool = False  # read only with a hint (an ambiguous date): the text layer proves nothing


def _blank_to_none(value: str | None) -> str | None:
    return value if value is not None and value.strip() else None


def _collapse(value: str) -> str:
    return " ".join(value.split())


def _parse_money(value: str | None, currency: str | None) -> _Parsed:
    if value is None:
        return _Parsed()
    if currency is None:
        return _Parsed(error=NormalizeError.NO_CURRENCY)
    try:
        money = parse_money(value, currency)
    except AmbiguousNumberError:
        return _Parsed(error=NormalizeError.AMBIGUOUS_NUMBER)
    except ValueError:
        return _Parsed(error=NormalizeError.UNPARSEABLE)
    return _Parsed(typed=money, text=str(money.minor))


def _parse_date(value: str | None, day_first: bool | None) -> _Parsed:
    if value is None:
        return _Parsed()
    weak = False
    try:
        try:
            d = parse_date(value)
        except AmbiguousDateError:
            if day_first is None:
                raise
            d, weak = parse_date(value, day_first=day_first), True
    except AmbiguousDateError:
        return _Parsed(error=NormalizeError.AMBIGUOUS_DATE)
    except ValueError:
        return _Parsed(error=NormalizeError.UNPARSEABLE)
    return _Parsed(typed=d, text=d.isoformat(), weak=weak)


def _parse_decimal(value: str | None, parser: Callable[[str], Decimal]) -> _Parsed:
    if value is None:
        return _Parsed()
    try:
        d = parser(value)
    except AmbiguousNumberError:
        return _Parsed(error=NormalizeError.AMBIGUOUS_NUMBER)
    except ValueError:
        return _Parsed(error=NormalizeError.UNPARSEABLE)
    return _Parsed(typed=d, text=format(d.normalize(), "f"))


def _parse_currency(value: str | None, hint: str | None) -> _Parsed:
    if value is None:
        return _Parsed()
    try:
        code = normalize_currency(value, hint=hint)
    except AmbiguousCurrencyError:
        return _Parsed(error=NormalizeError.AMBIGUOUS_CURRENCY)
    except ValueError:
        return _Parsed(error=NormalizeError.UNSUPPORTED_CURRENCY)
    return _Parsed(typed=code, text=code)


def _parse_text(name: str, value: str | None) -> _Parsed:
    if value is None:
        return _Parsed()
    if name == "supplier_name":
        return _Parsed(typed=_collapse(value), text=normalize_supplier_name(value))
    if name == "supplier_bank_account":
        return _Parsed(typed=value, text=normalize_bank_account(value))
    if name == "supplier_tax_id":
        return _Parsed(typed=_collapse(value), text=_collapse(value).upper())
    return _Parsed(typed=_collapse(value), text=_collapse(value))


def _money_of(p: _Parsed) -> Money | None:
    return p.typed if isinstance(p.typed, Money) else None


def _decimal_of(p: _Parsed) -> Decimal | None:
    return p.typed if isinstance(p.typed, Decimal) else None


def _text_agrees(name: str, p: _Parsed, currency: str | None, text: str) -> bool | None:
    """Does the normalized value appear in the text layer? None when that proves nothing (not
    applicable, a value read only through a hint, or a number too small to be evidence)."""
    if p.error or p.typed is None or p.weak or name == "supplier_bank_account":
        return None
    typed = p.typed
    if isinstance(typed, Money):
        return amount_in_text(typed.minor, exponent(typed.currency), text)
    if isinstance(typed, date):
        return date_in_text(typed, text)
    if name == "currency" and currency:
        return currency_agreement(currency, text)
    if name.endswith("quantity") and isinstance(typed, Decimal):
        return None if abs(typed) < 10 else number_in_text(typed, text)  # a lone "1" is everywhere
    if name.endswith("tax_rate") and isinstance(typed, Decimal):
        return percent_in_text(typed, text)
    if isinstance(typed, str):
        return text_in_text(typed, text)
    return None


def _result(
    name: str,
    raw: RawField,
    parsed: _Parsed,
    *,
    text: str,
    use_text: bool,
    currency: str | None,
    rule: bool | None = None,
    master: bool | None = None,
) -> FieldResult:
    if parsed.error:
        return FieldResult(
            name, raw.value, None, _ZERO,
            {"self_confidence": raw.self_confidence.value, "normalize_error": parsed.error},
            raw.page,
        )  # fmt: skip
    if parsed.typed is None:
        return FieldResult(
            name, None, None, _ZERO,
            {"self_confidence": raw.self_confidence.value, "missing": True},
            None,
        )  # fmt: skip
    agrees = _text_agrees(name, parsed, currency, text) if use_text else None
    scored = score_field(Signals(raw.self_confidence, agrees, rule, master))
    return FieldResult(name, raw.value, parsed.text, scored.confidence, scored.signals, raw.page)


def _relation(qty: Decimal | None, unit: Money | None, amount: Money | None) -> bool | None:
    if qty is None or unit is None or amount is None:
        return None
    return sum_supports(amount.minor, [line_amount_minor(qty, unit.minor)], tolerance_minor=1)


def interpret(
    raw: RawInvoice,
    *,
    doc_quality: DocQuality,
    page_texts: Sequence[str],
    master: MasterData | None = None,
    day_first: bool | None = None,
) -> Interpreted:
    master = master or MasterData()
    text = "\n".join(page_texts)
    use_text = doc_quality is DocQuality.CLEAN and bool(text.strip())
    missing = RawField(None, SelfConfidence.LOW, None)

    def value(f: RawField) -> str | None:
        return _blank_to_none(f.value)

    def clean(f: RawField) -> RawField:
        return RawField(value(f), f.self_confidence, f.page)

    header = {k: clean(v) for k, v in raw.header.items()}
    cur = _parse_currency(header.get("currency", missing).value, master.default_currency)
    currency = cur.typed if isinstance(cur.typed, str) else None

    parsed: dict[str, _Parsed] = {}
    for name, f in header.items():
        if name == "currency":
            parsed[name] = cur
        elif name in _MONEY_FIELDS:
            parsed[name] = _parse_money(f.value, currency)
        elif name in _DATE_FIELDS:
            parsed[name] = _parse_date(f.value, day_first)
        else:
            parsed[name] = _parse_text(name, f.value)

    # ---- lines
    line_parsed: list[dict[str, _Parsed]] = []
    for line in raw.lines:
        lp: dict[str, _Parsed] = {}
        for name, f in line.items():
            v = value(f)
            if name == "quantity":
                lp[name] = _parse_decimal(v, parse_quantity)
            elif name == "tax_rate":
                lp[name] = _parse_decimal(v, parse_tax_rate)
            elif name in ("unit_price", "amount"):
                lp[name] = _parse_money(v, currency)
            else:
                lp[name] = _parse_text(name, v)
        line_parsed.append(lp)

    # ---- rule support
    sub = _money_of(parsed.get("subtotal", _Parsed()))
    tax = _money_of(parsed.get("tax_total", _Parsed()))
    tot = _money_of(parsed.get("total", _Parsed()))
    totals_ok = (
        sum_supports(tot.minor, [sub.minor, tax.minor])
        if sub is not None and tax is not None and tot is not None
        else None
    )
    amounts = [_money_of(lp.get("amount", _Parsed())) for lp in line_parsed]
    lines_ok = (
        sum_supports(sub.minor, [a.minor for a in amounts if a is not None])
        if sub is not None and amounts and all(a is not None for a in amounts)
        else None
    )
    subtotal_rule = (
        lines_ok if totals_ok is None else totals_ok if lines_ok is None else lines_ok and totals_ok
    )
    header_rules = {"total": totals_ok, "tax_total": totals_ok, "subtotal": subtotal_rule}
    masters: dict[str, bool | None] = {
        "supplier_name": True if master.name_known else None,
        "supplier_tax_id": True if master.tax_id_known else None,
    }
    if currency and master.default_currency:  # the supplier's usual currency backs or contradicts
        masters["currency"] = master.default_currency == currency

    fields: list[FieldResult] = []
    for name, f in header.items():
        fields.append(
            _result(
                name, f, parsed[name], text=text, use_text=use_text, currency=currency,
                rule=header_rules.get(name), master=masters.get(name),
            )
        )  # fmt: skip

    lines: list[LineResult] = []
    for n, (line, lp) in enumerate(zip(raw.lines, line_parsed, strict=True), start=1):
        rel = _relation(
            _decimal_of(lp.get("quantity", _Parsed())),
            _money_of(lp.get("unit_price", _Parsed())),
            _money_of(lp.get("amount", _Parsed())),
        )
        line_rules = {"quantity": rel, "unit_price": rel, "amount": rel}
        for name, f in line.items():
            fields.append(
                _result(
                    line_field_name(n, name), clean(f), lp[name], text=text, use_text=use_text,
                    currency=currency, rule=line_rules.get(name),
                )
            )  # fmt: skip
        qty = _decimal_of(lp.get("quantity", _Parsed()))
        rate = _decimal_of(lp.get("tax_rate", _Parsed()))
        unit = _money_of(lp.get("unit_price", _Parsed()))
        amount = _money_of(lp.get("amount", _Parsed()))
        lines.append(
            LineResult(
                line_no=n,
                description=_as_str(lp.get("description", _Parsed()).typed),
                sku=_as_str(lp.get("sku", _Parsed()).typed),
                quantity=qty,
                unit_price_minor=unit.minor if unit else None,
                amount_minor=amount.minor if amount else None,
                tax_rate=rate,
            )
        )

    def head_text(name: str) -> str | None:
        return _as_str(parsed.get(name, _Parsed()).typed)

    def head_date(name: str) -> date | None:
        d = parsed.get(name, _Parsed()).typed
        return d if isinstance(d, date) else None

    return Interpreted(
        supplier_name=head_text("supplier_name"),
        invoice_number=head_text("invoice_number"),
        po_number=head_text("po_number"),
        payment_terms=head_text("payment_terms"),
        invoice_date=head_date("invoice_date"),
        due_date=head_date("due_date"),
        currency=currency,
        subtotal_minor=sub.minor if sub else None,
        tax_minor=tax.minor if tax else None,
        total_minor=tot.minor if tot else None,
        lines=lines,
        fields=fields,
    )


def _as_str(v: object | None) -> str | None:
    return v if isinstance(v, str) else None
