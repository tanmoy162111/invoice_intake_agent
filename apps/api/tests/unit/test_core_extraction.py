from datetime import date
from decimal import Decimal

from intake.core.confidence import SelfConfidence as C
from intake.core.extraction import (
    MasterData,
    RawField,
    RawInvoice,
    interpret,
    line_field_name,
)

HEADER = [
    "supplier_name",
    "supplier_tax_id",
    "supplier_address",
    "supplier_bank_account",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "currency",
    "subtotal",
    "tax_total",
    "total",
    "payment_terms",
]
D = Decimal

TEXT = """INVOICE
Brightline Office Supplies Inc.
Tax ID: 69-9397618
Invoice # BL-2026-0540
Invoice Date 2026-07-26
Due Date 2026-08-25
PO Number PO-10089
Terms Net 30
Ballpoint pens, box of 50 10 $12.90 $129.00
Toner cartridge, black 10 $89.50 $895.00
Subtotal $1,024.00
Tax (8%) $81.92
Total $1,105.92
Bank details
ABA 533367701 / Acct 1472427652
"""


def f(value: str | None, conf: C = C.HIGH, page: int | None = 1) -> RawField:
    return RawField(value, conf, page if value is not None else None)


def raw(**over: RawField | None) -> RawInvoice:
    header = {
        "supplier_name": f("Brightline Office Supplies Inc."),
        "supplier_tax_id": f("69-9397618"),
        "supplier_address": f(None),
        "supplier_bank_account": f("ABA 533367701 / Acct 1472427652"),
        "invoice_number": f("BL-2026-0540"),
        "invoice_date": f("2026-07-26"),
        "due_date": f("2026-08-25"),
        "po_number": f("PO-10089"),
        "currency": f("$"),
        "subtotal": f("$1,024.00"),
        "tax_total": f("$81.92"),
        "total": f("$1,105.92"),
        "payment_terms": f("Net 30"),
    }
    for k, v in over.items():
        header[k] = v if v is not None else f(None)
    lines = [
        {
            "description": f("Ballpoint pens, box of 50"),
            "sku": f(None),
            "quantity": f("10"),
            "unit_price": f("$12.90"),
            "amount": f("$129.00"),
            "tax_rate": f(None),
        },
        {
            "description": f("Toner cartridge, black"),
            "sku": f(None),
            "quantity": f("10"),
            "unit_price": f("$89.50"),
            "amount": f("$895.00"),
            "tax_rate": f(None),
        },
    ]
    return RawInvoice(header, lines)


def run(r: RawInvoice | None = None, quality: str = "clean", text: str = TEXT, **kw: object):  # type: ignore[no-untyped-def]
    return interpret(r or raw(), doc_quality=quality, page_texts=[text], **kw)  # type: ignore[arg-type]


def field(res, name: str):  # type: ignore[no-untyped-def]
    return next(x for x in res.fields if x.field == name)


def test_typed_values_are_normalized() -> None:
    res = run()
    assert res.invoice_date == date(2026, 7, 26)
    assert res.due_date == date(2026, 8, 25)
    assert res.currency == "USD"
    assert (res.subtotal_minor, res.tax_minor, res.total_minor) == (102400, 8192, 110592)
    assert res.supplier_name == "Brightline Office Supplies Inc."
    assert res.invoice_number == "BL-2026-0540"
    assert res.po_number == "PO-10089"
    assert res.payment_terms == "Net 30"


def test_lines_are_normalized_in_minor_units() -> None:
    res = run()
    assert [
        (ln.line_no, ln.quantity, ln.unit_price_minor, ln.amount_minor) for ln in res.lines
    ] == [
        (1, D("10"), 1290, 12900),
        (2, D("10"), 8950, 89500),
    ]
    assert res.lines[0].description == "Ballpoint pens, box of 50"


def test_european_number_format_and_dates() -> None:
    r = raw(
        currency=f("EUR"),
        invoice_date=f("13.03.2026"),
        subtotal=f("6.403,40 EUR"),
        tax_total=f("1.216,65 EUR"),
        total=f("7.620,05 EUR"),
    )
    res = run(r, text="")
    assert res.invoice_date == date(2026, 3, 13)
    assert res.total_minor == 762005
    assert res.subtotal_minor == 640340


def test_missing_po_number_stays_null() -> None:
    res = run(raw(po_number=None))
    assert res.po_number is None
    po = field(res, "po_number")
    assert (po.raw, po.normalized, po.confidence) == (None, None, D(0))


def test_field_results_cover_every_header_field_and_line_field() -> None:
    res = run()
    names = {x.field for x in res.fields}
    assert set(HEADER) <= names
    assert line_field_name(1, "amount") == "line.1.amount"
    assert "line.2.unit_price" in names
    assert len(res.fields) == len(HEADER) + 2 * 6


def test_clean_doc_with_text_agreement_is_trusted() -> None:
    res = run()
    for name in ("invoice_number", "invoice_date", "total", "currency", "supplier_name"):
        fr = field(res, name)
        assert fr.confidence >= D("0.8"), name
        assert fr.signals["text_layer"] is True


def test_value_missing_from_text_layer_is_contradicted() -> None:
    res = run(raw(invoice_number=f("BL-2026-9999")))
    fr = field(res, "invoice_number")
    assert fr.signals["text_layer"] is False
    assert fr.confidence < D("0.8")


def test_scanned_doc_has_no_text_layer_signal() -> None:
    res = run(quality="scanned", text="")
    fr = field(res, "invoice_number")
    assert fr.signals["text_layer"] is None
    assert fr.confidence < D("0.8")  # high self-confidence alone is not enough


def test_scanned_total_is_trusted_when_the_lines_add_up() -> None:
    res = run(quality="scanned", text="")
    fr = field(res, "total")
    assert fr.signals["rule"] is True
    assert fr.confidence >= D("0.8")


def test_totals_that_do_not_add_up_are_contradicted() -> None:
    res = run(raw(total=f("$1,200.00")), text="")
    assert field(res, "total").signals["rule"] is False
    assert field(res, "total").confidence < D("0.8")


def test_line_math_supports_and_contradicts() -> None:
    good = run()
    assert field(good, "line.1.amount").signals["rule"] is True
    r = raw()
    bad_line = dict(r.lines[0])
    bad_line["amount"] = f("$130.00")
    res = run(RawInvoice(r.header, [bad_line, r.lines[1]]))
    assert field(res, "line.1.amount").signals["rule"] is False
    assert field(res, "line.1.amount").confidence < D("0.8")


def test_known_supplier_supports_name_and_tax_id() -> None:
    res = run(quality="scanned", text="", master=MasterData(tax_id_known=True, name_known=True))
    assert field(res, "supplier_name").signals["master_data"] is True
    assert field(res, "supplier_tax_id").signals["master_data"] is True


def test_unknown_supplier_is_not_a_contradiction_here() -> None:
    # UNKNOWN_SUPPLIER is a validation rule (M4); extraction just has no master-data support.
    res = run(master=MasterData())
    assert field(res, "supplier_name").signals["master_data"] is None


def test_ambiguous_date_is_not_guessed() -> None:
    res = run(raw(invoice_date=f("03/04/2026")), text="")
    assert res.invoice_date is None
    fr = field(res, "invoice_date")
    assert fr.normalized is None
    assert fr.confidence == D(0)
    assert fr.signals["normalize_error"] == "AMBIGUOUS_DATE"


def test_day_first_hint_resolves_ambiguity() -> None:
    res = run(raw(invoice_date=f("03/04/2026")), text="", day_first=True)
    assert res.invoice_date == date(2026, 4, 3)


def test_unparseable_amount_is_null_with_zero_confidence() -> None:
    res = run(raw(total=f("about a thousand")), text="")
    assert res.total_minor is None
    assert field(res, "total").signals["normalize_error"] == "UNPARSEABLE"
    assert field(res, "total").confidence == D(0)


def test_unsupported_currency_blocks_money_fields() -> None:
    res = run(raw(currency=f("XYZ")), text="")
    assert res.currency is None
    assert res.total_minor is None
    assert field(res, "total").signals["normalize_error"] == "NO_CURRENCY"
    assert field(res, "currency").signals["normalize_error"] == "UNSUPPORTED_CURRENCY"


def test_bank_account_is_normalized_but_has_no_text_signal() -> None:
    fr = field(run(), "supplier_bank_account")
    assert fr.raw == "ABA 533367701 / Acct 1472427652"
    assert fr.normalized == "ABA533367701ACCT1472427652"
    assert fr.signals["text_layer"] is None


def test_low_self_confidence_never_clears() -> None:
    res = run(raw(total=f("$1,105.92", C.LOW)))
    assert field(res, "total").confidence < D("0.8")


def test_signals_are_json_serializable() -> None:
    import json

    json.dumps([x.signals for x in run().fields])


def test_no_lines_is_fine() -> None:
    r = raw()
    res = run(RawInvoice(r.header, []))
    assert res.lines == []
    assert len(res.fields) == len(HEADER)


def test_garbage_date_quantity_and_tax_rate_are_unparseable() -> None:
    r = raw(due_date=f("soonish"))
    line = dict(r.lines[0])
    line["quantity"] = f("a dozen")
    line["tax_rate"] = f("150%")
    res = run(RawInvoice(r.header, [line]), text="")
    assert field(res, "due_date").signals["normalize_error"] == "UNPARSEABLE"
    assert field(res, "line.1.quantity").signals["normalize_error"] == "UNPARSEABLE"
    assert field(res, "line.1.tax_rate").signals["normalize_error"] == "UNPARSEABLE"
    assert res.lines[0].quantity is None
    assert res.lines[0].tax_rate is None


def test_absent_optional_values_normalize_to_none() -> None:
    res = run(raw(due_date=None, subtotal=None, tax_total=None, currency=None), text="")
    assert (res.due_date, res.subtotal_minor, res.tax_minor, res.currency) == (
        None,
        None,
        None,
        None,
    )
    # with no currency, the money fields that are present can't be read
    assert res.total_minor is None


def test_quantity_and_tax_rate_are_checked_against_the_text_layer() -> None:
    r = raw()
    line = dict(r.lines[0])
    line["tax_rate"] = f("8%")
    res = run(RawInvoice(r.header, [line]))
    assert field(res, "line.1.tax_rate").signals["text_layer"] is True
    assert field(res, "line.1.quantity").signals["text_layer"] is True
    assert res.lines[0].tax_rate == D("8")
    line["quantity"] = f("77")
    res = run(RawInvoice(r.header, [line]))
    assert field(res, "line.1.quantity").signals["text_layer"] is False


def test_blank_strings_count_as_missing() -> None:
    res = run(raw(po_number=RawField("   ", C.HIGH, 1)))
    assert res.po_number is None
    assert field(res, "po_number").signals.get("missing") is True


def test_mismatched_currency_code_in_an_amount_is_unparseable() -> None:
    res = run(raw(total=f("1,105.92 EUR")), text="")
    assert res.total_minor is None
    assert field(res, "total").signals["normalize_error"] == "UNPARSEABLE"
