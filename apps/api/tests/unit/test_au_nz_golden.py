"""The Australian and New Zealand golden supplement (data/golden/au_nz): what is on disk matches the
generator, the arithmetic and tax numbers are right, each document prints money the way its answer
key says, and the main golden set is not touched.

The last tests state what the system does with a printed `$`, `A$` and `NZ$`."""

import json
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import pdfplumber
import pytest
from generator import au_nz
from generator.generate import GOLDEN_DIR, truth_for
from generator.render import fmt_amount
from generator.world import tax_of

from intake.core.money import CURRENCY_EXPONENT, Money, line_amount_minor, parse_money
from intake.core.normalize import normalize_currency

OUT = GOLDEN_DIR / "au_nz"


@cache
def world() -> Any:
    return au_nz.build()


def truths() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((OUT / "truth").glob("*.json"))]


MASTER = json.loads((OUT / "master.json").read_text())
SUPPLIERS = {s["key"]: s for s in MASTER["suppliers"]}
POS = {po["po_number"]: po for po in MASTER["purchase_orders"]}


# ---- what is on disk is what the generator makes --------------------------------------------


def test_the_supplement_has_four_aud_and_four_nzd_invoices_in_every_quality() -> None:
    ts = truths()
    assert len(ts) == 8
    assert sorted(t["header"]["currency"] for t in ts) == ["AUD"] * 4 + ["NZD"] * 4
    assert {t["doc_quality"] for t in ts} == {"clean", "scanned", "photo"}
    assert all(t["supplement"] == "au_nz" and t["split"] == "golden" for t in ts)


def test_the_generator_reproduces_every_committed_answer_key() -> None:
    for inv in world().invoices:
        committed = json.loads((OUT / "truth" / f"{inv.id}.json").read_text())
        fresh = truth_for(inv, world(), au_nz.file_name(inv), "golden")
        fresh["supplement"] = "au_nz"
        assert committed == fresh, inv.id


def test_every_answer_key_has_its_document() -> None:
    for t in truths():
        assert (OUT / "invoices" / t["file"]).is_file(), t["file"]


def test_the_master_data_is_the_generators() -> None:
    assert au_nz.supplement_master(world()) == MASTER


def test_the_main_golden_set_is_untouched_and_does_not_see_the_supplement() -> None:
    top = {p.stem for p in (GOLDEN_DIR / "truth").glob("*.json")}
    assert len(top) == 60
    assert not any(t["id"] in top for t in truths())
    assert not list((GOLDEN_DIR / "invoices").glob("*Wattle*"))


# ---- the numbers ----------------------------------------------------------------------------


@pytest.mark.parametrize("truth", truths(), ids=lambda t: t["id"])
def test_lines_subtotal_tax_and_total_add_up_exactly(truth: dict[str, Any]) -> None:
    h, cur = truth["header"], truth["header"]["currency"]
    assert cur in CURRENCY_EXPONENT
    rate = {"AUD": 1000, "NZD": 1500}[cur]
    for ln in truth["lines"]:
        assert (
            line_amount_minor(Decimal(ln["quantity"]), ln["unit_price_minor"]) == ln["amount_minor"]
        )
    subtotal = sum(ln["amount_minor"] for ln in truth["lines"])
    assert h["subtotal_minor"] == subtotal
    assert h["tax_total_minor"] == tax_of(subtotal, rate)
    assert h["total_minor"] == subtotal + h["tax_total_minor"]


@pytest.mark.parametrize("truth", truths(), ids=lambda t: t["id"])
def test_each_invoice_has_its_purchase_order_and_a_full_receipt_before_the_invoice_date(
    truth: dict[str, Any],
) -> None:
    po = POS[truth["header"]["po_number"]]
    assert po["currency"] == truth["header"]["currency"]
    assert po["total_minor"] == truth["header"]["subtotal_minor"]
    assert SUPPLIERS[truth["supplier_key"]]["default_currency"] == po["currency"]
    [receipt] = po["receipts"]
    assert receipt["received_at"] < truth["header"]["invoice_date"]
    assert {ln["line_no"]: ln["qty_received"] for ln in receipt["lines"]} == {
        ln["line_no"]: ln["qty"] for ln in po["lines"]
    }
    assert truth["expected"] == {"must_raise": [], "may_raise": [], "should_clear": True}


def test_tax_numbers_pass_the_real_check_digit_rules() -> None:
    for s in SUPPLIERS.values():
        if s["default_currency"] == "AUD":
            assert au_nz.valid_abn(s["tax_id"]), s["name"]
        else:
            assert au_nz.valid_ird(s["tax_id"]), s["name"]
    assert not au_nz.valid_abn("51 824 753 557")
    assert not au_nz.valid_ird("123-456-789")


def test_each_tax_number_is_unique_and_dates_are_recent_and_ordered() -> None:
    assert len({s["tax_id"] for s in SUPPLIERS.values()}) == len(SUPPLIERS)
    for t in truths():
        h = t["header"]
        assert "2026-09-01" <= h["invoice_date"] <= "2026-10-20"
        assert h["invoice_date"] < h["due_date"]


# ---- each document prints money the way its answer key says ---------------------------------


def printed(file: str) -> str:
    with pdfplumber.open(OUT / "invoices" / file) as pdf:
        return pdf.pages[0].extract_text() or ""


@pytest.mark.parametrize(
    "truth", [t for t in truths() if t["doc_quality"] == "clean"], ids=lambda t: t["id"]
)
def test_a_clean_document_prints_its_total_in_its_style(truth: dict[str, Any]) -> None:
    inv = next(i for i in world().invoices if i.id == truth["id"])
    text = printed(truth["file"])
    total = fmt_amount(inv.total_minor, inv.currency, inv.money_style)
    assert total in text
    assert "GST (" in text  # the tax line says GST, not "Tax"
    prefix = {"AUD": "A$", "NZD": "NZ$"}[inv.currency]
    if inv.money_style == "prefixed":
        assert prefix in text
    elif inv.money_style == "code":
        assert f"{inv.currency} " in text and "$" not in text
    else:
        assert "$" in text and prefix not in text and inv.currency not in text


def test_all_three_ways_of_printing_money_are_covered_in_both_currencies() -> None:
    seen = {(i.currency, i.money_style) for i in world().invoices}
    for cur in ("AUD", "NZD"):
        assert {(cur, "symbol"), (cur, "prefixed"), (cur, "code")} <= seen


def test_the_new_amount_styles_and_the_old_ones() -> None:
    assert fmt_amount(123450, "AUD") == "$1,234.50"
    assert fmt_amount(123450, "AUD", "prefixed") == "A$1,234.50"
    assert fmt_amount(123450, "NZD", "prefixed") == "NZ$1,234.50"
    assert fmt_amount(123450, "NZD", "code") == "NZD 1,234.50"
    assert fmt_amount(-5, "AUD", "prefixed") == "-A$0.05"
    # unchanged for the existing currencies, whatever the style
    assert fmt_amount(123450, "USD") == "$1,234.50"
    assert fmt_amount(123450, "GBP") == "£1,234.50"
    assert fmt_amount(123450, "EUR") == "1.234,50 EUR"
    assert fmt_amount(123450, "USD", "code") == "$1,234.50"


def test_the_supplement_master_adds_aud_and_nzd_limits_and_keeps_the_seeds() -> None:
    limits = MASTER["tenant"]["settings"]["approval_amount_limits_minor"]
    assert limits["AUD"] == limits["NZD"] == 1_000_000
    assert limits["EUR"] == limits["GBP"] == 1_000_000
    seed = json.loads((GOLDEN_DIR.parent / "seed" / "master.json").read_text())
    assert MASTER["tenant"]["id"] == seed["tenant"]["id"]
    assert not {s["key"] for s in seed["suppliers"]} & set(SUPPLIERS)


def test_generating_again_changes_nothing(tmp_path: Path) -> None:
    before = {p: p.read_bytes() for p in OUT.rglob("*") if p.is_file()}
    au_nz.main()
    after = {p: p.read_bytes() for p in OUT.rglob("*") if p.is_file()}
    assert before == after


# ---- what the system does with them ---------------------------------------------------


@pytest.mark.parametrize("code", ["AUD", "NZD"])
def test_a_printed_code_works(code: str) -> None:
    assert normalize_currency(code) == code
    assert parse_money(f"{code} 1,234.50", code) == Money(123450, code)
    assert parse_money("1,234.50", code) == Money(123450, code)


@pytest.mark.parametrize("code", ["AUD", "NZD"])
def test_a_dollar_sign_is_read_as_the_suppliers_own_dollar(code: str) -> None:
    assert normalize_currency("$", hint=code) == code
    assert parse_money("$1,234.50", code) == Money(123450, code)


@pytest.mark.parametrize(("text", "code"), [("A$", "AUD"), ("NZ$", "NZD")])
def test_a_prefixed_dollar_sign_names_its_currency(text: str, code: str) -> None:
    assert normalize_currency(text) == code
    assert parse_money(f"{text}1,234.50", code) == Money(123450, code)
