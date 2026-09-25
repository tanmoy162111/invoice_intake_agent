from dataclasses import replace
from decimal import Decimal

import pytest

from intake.core.match import (
    RULE_VERSION,
    InvoiceLineFacts,
    MatchCode,
    MatchInvoice,
    MatchResult,
    MatchSettings,
    PoLineRef,
    PoRef,
    check_match,
    normalize_po_number,
)
from intake.core.validate import Outcome

SUP = "sup-1"
D = Decimal


def po_line(
    n: int,
    sku: str | None,
    desc: str,
    qty: int = 10,
    price: int = 1000,
    received: int | None = None,
    billed_before: int = 0,
) -> PoLineRef:
    return PoLineRef(
        id=f"pl{n}",
        line_no=n,
        sku=sku,
        description=desc,
        qty=D(qty),
        unit_price_minor=price,
        qty_received=D(qty if received is None else received),
        qty_billed_before=D(billed_before),
    )


LINES = (
    po_line(1, "A-100", "Blue widget, box of 10", 10, 1000),
    po_line(2, "B-200", "Steel bracket heavy duty", 5, 2000),
)
PO = PoRef(
    id="po1",
    po_number="PO-7781",
    supplier_id=SUP,
    currency="USD",
    total_minor=20000,
    lines=LINES,
    receipt_count=1,
)


def iline(
    n: int,
    sku: str | None,
    desc: str | None,
    qty: int | None = 10,
    price: int | None = 1000,
    amount: int | None = None,
) -> InvoiceLineFacts:
    amt = amount if amount is not None else (qty * price if qty is not None and price else None)
    return InvoiceLineFacts(n, sku, desc, None if qty is None else D(qty), price, amt)


CLEAN_LINES = (
    iline(1, "A-100", "Blue widget, box of 10", 10, 1000),
    iline(2, "B-200", "Steel bracket heavy duty", 5, 2000),
)


def inv(
    po_number: str | None = "PO-7781",
    lines: tuple[InvoiceLineFacts, ...] = CLEAN_LINES,
    subtotal: int | None = 20000,
    currency: str | None = "USD",
    supplier_id: str | None = SUP,
) -> MatchInvoice:
    return MatchInvoice("i1", supplier_id, po_number, currency, subtotal, lines)


def run(
    invoice: MatchInvoice | None = None,
    pos: list[PoRef] | None = None,
    settings: MatchSettings | None = None,
) -> MatchResult:
    return check_match(invoice or inv(), [PO] if pos is None else pos, settings or MatchSettings())


def out(result: MatchResult, code: MatchCode) -> Outcome:
    return next(c.outcome for c in result.checks if c.code is code)


def details(result: MatchResult, code: MatchCode) -> dict[str, object]:
    return next(c.details for c in result.checks if c.code is code)


# ---- the clean case ------------------------------------------------------------------------


def test_clean_invoice_passes_every_check() -> None:
    r = run()
    assert r.po_id == "po1" and not r.inferred
    assert {c.code for c in r.checks} == set(MatchCode)
    assert all(c.outcome is Outcome.PASS for c in r.checks)
    assert r.version == RULE_VERSION and r.passed


# ---- finding the PO -------------------------------------------------------------------------


@pytest.mark.parametrize("printed", ["po-7781", "PO 7781", " PO7781 "])
def test_po_number_is_normalized(printed: str) -> None:
    assert run(inv(po_number=printed)).po_id == "po1"


def test_normalize_po_number() -> None:
    assert normalize_po_number("po 7781") == normalize_po_number("PO-7781")
    assert normalize_po_number(None) == ""


def test_unknown_po_number_fails_po_not_found_and_skips_line_checks() -> None:
    r = run(inv(po_number="PO-94281"))
    assert r.po_id is None
    assert out(r, MatchCode.PO_NOT_FOUND) is Outcome.FAIL
    assert details(r, MatchCode.PO_NOT_FOUND)["po_number"] == "PO-94281"
    for code in (
        MatchCode.PRICE_VARIANCE,
        MatchCode.QTY_VARIANCE,
        MatchCode.RECEIPT_MISSING,
        MatchCode.QTY_NOT_RECEIVED,
        MatchCode.PO_OVERBILLED,
    ):
        assert out(r, code) is Outcome.SKIPPED
        assert details(r, code)["reason"] == "NO_PO"
    assert not r.passed


def test_po_of_another_supplier_is_not_found() -> None:
    r = run(inv(supplier_id="sup-2"))
    assert out(r, MatchCode.PO_NOT_FOUND) is Outcome.FAIL
    assert details(r, MatchCode.PO_NOT_FOUND)["reason"] == "PO_OF_OTHER_SUPPLIER"


def test_named_po_with_an_unresolved_supplier_is_uncertain_not_matched() -> None:
    r = run(inv(supplier_id=None))
    assert r.po_id is None
    assert out(r, MatchCode.PO_NOT_FOUND) is Outcome.SKIPPED
    assert details(r, MatchCode.PO_NOT_FOUND)["reason"] == "SUPPLIER_UNKNOWN"
    assert not r.passed


@pytest.mark.parametrize("status", ["closed", "cancelled"])
def test_named_po_that_is_not_open_fails_po_not_found(status: str) -> None:
    r = run(pos=[replace(PO, status=status)])
    assert r.po_id is None
    assert out(r, MatchCode.PO_NOT_FOUND) is Outcome.FAIL
    assert details(r, MatchCode.PO_NOT_FOUND)["reason"] == "PO_NOT_OPEN"
    assert not r.passed


def test_two_pos_with_the_same_normalized_number_are_not_guessed() -> None:
    r = run(pos=[replace(PO, id="a", po_number="PO-7781"), replace(PO, id="b", po_number="PO7781")])
    assert r.po_id is None
    assert out(r, MatchCode.NO_PO) is Outcome.SKIPPED
    assert details(r, MatchCode.NO_PO)["reason"] == "AMBIGUOUS_PO"


def test_an_inferred_po_is_matched_but_never_passes_clean() -> None:
    r = run(inv(po_number=None, subtotal=20100))  # +0.5%
    assert r.po_id == "po1" and r.inferred
    assert out(r, MatchCode.NO_PO) is Outcome.SKIPPED
    assert details(r, MatchCode.NO_PO) == {"reason": "PO_INFERRED", "inferred_po_number": "PO-7781"}
    assert not r.passed


def test_no_po_number_and_no_candidate_fails_no_po() -> None:
    r = run(inv(po_number=None, subtotal=99999))
    assert r.po_id is None
    assert out(r, MatchCode.NO_PO) is Outcome.FAIL


def test_no_po_number_ignores_closed_other_currency_and_other_supplier() -> None:
    others = [
        replace(PO, id="c", status="closed"),
        replace(PO, id="e", currency="EUR"),
        replace(PO, id="s", supplier_id="sup-2"),
    ]
    r = run(inv(po_number=None), pos=others)
    assert out(r, MatchCode.NO_PO) is Outcome.FAIL


def test_no_po_number_with_two_candidates_is_uncertain_not_guessed() -> None:
    r = run(inv(po_number=None), pos=[PO, replace(PO, id="po2", po_number="PO-7782")])
    assert r.po_id is None
    assert out(r, MatchCode.NO_PO) is Outcome.SKIPPED
    assert details(r, MatchCode.NO_PO)["reason"] == "AMBIGUOUS_PO"


def test_no_po_number_needs_a_supplier_and_a_subtotal() -> None:
    assert out(run(inv(po_number=None, supplier_id=None)), MatchCode.NO_PO) is Outcome.SKIPPED
    assert out(run(inv(po_number=None, subtotal=None)), MatchCode.NO_PO) is Outcome.SKIPPED


def test_a_po_that_was_found_by_number_does_not_report_no_po() -> None:
    assert out(run(), MatchCode.NO_PO) is Outcome.PASS


# ---- matching lines -------------------------------------------------------------------------


def test_lines_match_by_sku_even_when_descriptions_differ() -> None:
    lines = (iline(1, "a-100", "Totally different words"), iline(2, "B-200", None, 5, 2000))
    r = run(inv(lines=lines))
    assert [(m.invoice_line_no, m.po_line_id, m.method) for m in r.line_matches] == [
        (1, "pl1", "sku"),
        (2, "pl2", "sku"),
    ]
    assert r.passed


def test_lines_match_by_similar_description_when_no_sku() -> None:
    lines = (
        iline(1, None, "Blue widgets - box of 10"),
        iline(2, None, "Heavy duty steel bracket", 5, 2000),
    )
    r = run(inv(lines=lines))
    assert [m.method for m in r.line_matches] == ["description", "description"]
    assert [m.po_line_id for m in r.line_matches] == ["pl1", "pl2"]
    assert r.passed


def test_lines_match_by_amount_when_sku_and_description_fail() -> None:
    po_lines = (LINES[0], po_line(2, "B-200", "Steel bracket heavy duty", 5, 3000))
    lines = (iline(1, None, "zzz", 10, 1000), iline(2, None, "qqq", 5, 3000))
    r = run(inv(lines=lines, subtotal=25000), pos=[replace(PO, total_minor=25000, lines=po_lines)])
    assert [m.method for m in r.line_matches] == ["amount", "amount"]
    assert r.passed


def test_a_po_line_is_matched_at_most_once() -> None:
    lines = (
        iline(1, "A-100", "Blue widget, box of 10"),
        iline(2, "A-100", "Blue widget, box of 10", 3, 1000),
    )
    r = run(inv(lines=lines))
    assert [m.invoice_line_no for m in r.line_matches] == [1]


def test_a_tie_between_two_po_lines_is_not_guessed() -> None:
    twins = (
        po_line(1, None, "Cable tie pack 100 black"),
        po_line(2, None, "Cable tie pack 100 white"),
    )
    pos = [replace(PO, lines=twins)]
    lines = (iline(1, None, "Cable tie pack 100 grey", 10, 1000),)
    r = run(inv(lines=lines, subtotal=10000), pos=pos)
    assert r.line_matches == ()
    assert out(r, MatchCode.QTY_VARIANCE) is Outcome.FAIL
    assert details(r, MatchCode.QTY_VARIANCE)["findings"][0]["kind"] == "LINE_NOT_ON_PO"  # type: ignore[index]


def test_line_not_on_po_fails_qty_variance() -> None:
    lines = CLEAN_LINES + (iline(3, "Z-999", "Something else entirely", 1, 500),)
    r = run(inv(lines=lines, subtotal=20500))
    assert out(r, MatchCode.QTY_VARIANCE) is Outcome.FAIL
    finding = details(r, MatchCode.QTY_VARIANCE)["findings"][0]
    assert finding["kind"] == "LINE_NOT_ON_PO" and finding["line"] == 3  # type: ignore[index]


# ---- price ----------------------------------------------------------------------------------


def test_price_within_tolerance_passes_and_boundary_is_inclusive() -> None:
    lines = (iline(1, "A-100", None, 10, 1020), CLEAN_LINES[1])  # exactly +2%
    assert out(run(inv(lines=lines)), MatchCode.PRICE_VARIANCE) is Outcome.PASS


def test_price_above_tolerance_fails_with_line_and_percent() -> None:
    lines = (CLEAN_LINES[0], iline(2, "B-200", None, 5, 2140))  # +7%
    r = run(inv(lines=lines))
    assert out(r, MatchCode.PRICE_VARIANCE) is Outcome.FAIL
    f = details(r, MatchCode.PRICE_VARIANCE)["findings"][0]
    assert f == {  # type: ignore[comparison-overlap]
        "line": 2,
        "invoice_unit_price_minor": 2140,
        "po_unit_price_minor": 2000,
        "variance_bp": 700,
        "limit_bp": 200,
    }


def test_price_below_the_po_price_beyond_tolerance_also_fails() -> None:
    lines = (iline(1, "A-100", None, 10, 900), CLEAN_LINES[1])
    assert out(run(inv(lines=lines)), MatchCode.PRICE_VARIANCE) is Outcome.FAIL


def test_price_tolerance_comes_from_settings() -> None:
    lines = (iline(1, "A-100", None, 10, 1050), CLEAN_LINES[1])
    loose = MatchSettings(price_tolerance_bp=600)
    assert out(run(inv(lines=lines), settings=loose), MatchCode.PRICE_VARIANCE) is Outcome.PASS


def test_zero_po_price_any_price_fails() -> None:
    free = (po_line(1, "A-100", "Blue widget, box of 10", 10, 0),)
    r = run(inv(lines=(iline(1, "A-100", None, 10, 1),)), pos=[replace(PO, lines=free)])
    assert out(r, MatchCode.PRICE_VARIANCE) is Outcome.FAIL


def test_missing_unit_price_is_skipped_not_passed() -> None:
    lines = (iline(1, "A-100", None, 10, None, amount=10000), CLEAN_LINES[1])
    r = run(inv(lines=lines))
    assert out(r, MatchCode.PRICE_VARIANCE) is Outcome.SKIPPED
    assert details(r, MatchCode.PRICE_VARIANCE)["reason"] == "UNREADABLE_LINE"


# ---- quantity -------------------------------------------------------------------------------


def test_quantity_above_po_fails() -> None:
    lines = (iline(1, "A-100", None, 12, 1000), CLEAN_LINES[1])
    r = run(inv(lines=lines, subtotal=22000))
    assert out(r, MatchCode.QTY_VARIANCE) is Outcome.FAIL
    f = details(r, MatchCode.QTY_VARIANCE)["findings"][0]
    assert f["kind"] == "OVER_PO_QTY" and f["po_qty"] == "10" and f["billed_qty"] == "12"  # type: ignore[index]


def test_quantity_below_po_is_a_partial_invoice_and_passes() -> None:
    lines = (iline(1, "A-100", None, 4, 1000),)
    r = run(inv(lines=lines, subtotal=4000), pos=[replace(PO, lines=(LINES[0],))])
    assert out(r, MatchCode.QTY_VARIANCE) is Outcome.PASS


def test_quantity_counts_what_earlier_invoices_already_billed() -> None:
    billed = (po_line(1, "A-100", "Blue widget, box of 10", 10, 1000, billed_before=7),)
    r = run(
        inv(lines=(iline(1, "A-100", None, 4, 1000),), subtotal=4000),
        pos=[replace(PO, lines=billed, billed_before_minor=7000)],
    )
    assert out(r, MatchCode.QTY_VARIANCE) is Outcome.FAIL  # 7 + 4 > 10


def test_quantity_tolerance_from_settings() -> None:
    lines = (iline(1, "A-100", None, 11, 1000), CLEAN_LINES[1])
    tol = MatchSettings(qty_tolerance_bp=1000)  # 10% over is fine
    assert out(run(inv(lines=lines, subtotal=21000), settings=tol), MatchCode.QTY_VARIANCE) is (
        Outcome.PASS
    )


def test_missing_quantity_is_skipped_not_passed() -> None:
    lines = (iline(1, "A-100", None, None, 1000, amount=10000), CLEAN_LINES[1])
    r = run(inv(lines=lines))
    assert out(r, MatchCode.QTY_VARIANCE) is Outcome.SKIPPED
    assert out(r, MatchCode.QTY_NOT_RECEIVED) is Outcome.SKIPPED


# ---- receipts -------------------------------------------------------------------------------


def test_no_receipt_at_all_fails_receipt_missing_and_does_not_double_report() -> None:
    r = run(pos=[replace(PO, receipt_count=0)])
    assert out(r, MatchCode.RECEIPT_MISSING) is Outcome.FAIL
    assert details(r, MatchCode.RECEIPT_MISSING)["po_number"] == "PO-7781"
    assert out(r, MatchCode.QTY_NOT_RECEIVED) is Outcome.SKIPPED
    assert details(r, MatchCode.QTY_NOT_RECEIVED)["reason"] == "NO_RECEIPT"


def test_billing_more_than_received_fails_qty_not_received() -> None:
    part = (LINES[0], po_line(2, "B-200", "Steel bracket heavy duty", 5, 2000, received=3))
    r = run(pos=[replace(PO, lines=part)])
    assert out(r, MatchCode.RECEIPT_MISSING) is Outcome.PASS
    assert out(r, MatchCode.QTY_NOT_RECEIVED) is Outcome.FAIL
    f = details(r, MatchCode.QTY_NOT_RECEIVED)["findings"][0]
    assert f == {"line": 2, "billed_qty": "5", "received_qty": "3", "billed_before_qty": "0"}  # type: ignore[comparison-overlap]


def test_partial_receipt_covers_a_partial_invoice() -> None:
    part = (po_line(1, "A-100", "Blue widget, box of 10", 10, 1000, received=6),)
    r = run(
        inv(lines=(iline(1, "A-100", None, 6, 1000),), subtotal=6000),
        pos=[replace(PO, lines=part)],
    )
    assert out(r, MatchCode.QTY_NOT_RECEIVED) is Outcome.PASS


def test_second_invoice_cannot_bill_what_the_first_already_used_up() -> None:
    part = (po_line(1, "A-100", "Blue widget, box of 10", 10, 1000, received=6, billed_before=6),)
    r = run(
        inv(lines=(iline(1, "A-100", None, 2, 1000),), subtotal=2000),
        pos=[replace(PO, lines=part, billed_before_minor=6000)],
    )
    assert out(r, MatchCode.QTY_NOT_RECEIVED) is Outcome.FAIL
    assert details(r, MatchCode.QTY_NOT_RECEIVED)["findings"][0]["billed_before_qty"] == "6"  # type: ignore[index]


# ---- cumulative billing ---------------------------------------------------------------------


def test_cumulative_billing_over_the_po_total_fails() -> None:
    r = run(pos=[replace(PO, billed_before_minor=500)])
    assert out(r, MatchCode.PO_OVERBILLED) is Outcome.FAIL
    assert details(r, MatchCode.PO_OVERBILLED) == {
        "po_number": "PO-7781",
        "po_total_minor": 20000,
        "billed_before_minor": 500,
        "invoice_subtotal_minor": 20000,
        "billed_total_minor": 20500,
        "over_minor": 500,
        "currency": "USD",
    }


def test_cumulative_billing_exactly_the_po_total_passes() -> None:
    r = run(
        inv(lines=(iline(1, "A-100", None, 4, 1000),), subtotal=4000),
        pos=[replace(PO, billed_before_minor=16000)],
    )
    assert out(r, MatchCode.PO_OVERBILLED) is Outcome.PASS


def test_overbill_tolerance_from_settings() -> None:
    settings = MatchSettings(overbill_tolerance_minor=500)
    r = run(pos=[replace(PO, billed_before_minor=500)], settings=settings)
    assert out(r, MatchCode.PO_OVERBILLED) is Outcome.PASS


def test_subtotal_falls_back_to_summing_line_amounts() -> None:
    r = run(inv(subtotal=None), pos=[replace(PO, billed_before_minor=1)])
    assert out(r, MatchCode.PO_OVERBILLED) is Outcome.FAIL
    assert details(r, MatchCode.PO_OVERBILLED)["invoice_subtotal_minor"] == 20000


def test_no_subtotal_and_unreadable_lines_skip_the_overbilling_check() -> None:
    lines = (iline(1, "A-100", None, 10, 1000, amount=None), CLEAN_LINES[1])
    lines = (replace(lines[0], amount_minor=None), lines[1])
    r = run(inv(subtotal=None, lines=lines))
    assert out(r, MatchCode.PO_OVERBILLED) is Outcome.SKIPPED
    assert details(r, MatchCode.PO_OVERBILLED)["reason"] == "NO_SUBTOTAL"


# ---- currency and missing data --------------------------------------------------------------


def test_currency_differing_from_the_po_skips_amount_checks() -> None:
    r = run(inv(currency="EUR"))
    for code in (MatchCode.PRICE_VARIANCE, MatchCode.PO_OVERBILLED):
        assert out(r, code) is Outcome.SKIPPED
        assert details(r, code)["reason"] == "CURRENCY_DIFFERS_FROM_PO"
    assert not r.passed


def test_missing_currency_skips_amount_checks() -> None:
    assert out(run(inv(currency=None)), MatchCode.PO_OVERBILLED) is Outcome.SKIPPED


def test_invoice_with_no_lines_skips_line_checks_and_never_passes() -> None:
    r = run(inv(lines=()))
    for code in (MatchCode.PRICE_VARIANCE, MatchCode.QTY_VARIANCE, MatchCode.QTY_NOT_RECEIVED):
        assert out(r, code) is Outcome.SKIPPED
        assert details(r, code)["reason"] == "NO_LINES"
    assert not r.passed


def test_po_with_no_lines_skips_line_checks() -> None:
    r = run(pos=[replace(PO, lines=())])
    assert out(r, MatchCode.PRICE_VARIANCE) is Outcome.SKIPPED
    assert details(r, MatchCode.PRICE_VARIANCE)["reason"] == "PO_HAS_NO_LINES"


def test_one_failing_check_does_not_hide_another() -> None:
    lines = (iline(1, "A-100", None, 12, 1200), CLEAN_LINES[1])
    r = run(inv(lines=lines, subtotal=26400), pos=[replace(PO, receipt_count=0)])
    failed = {c.code for c in r.checks if c.outcome is Outcome.FAIL}
    assert failed == {
        MatchCode.PRICE_VARIANCE,
        MatchCode.QTY_VARIANCE,
        MatchCode.RECEIPT_MISSING,
        MatchCode.PO_OVERBILLED,
    }


# ---- settings -------------------------------------------------------------------------------


def test_settings_defaults_and_from_tenant() -> None:
    d = MatchSettings()
    assert (d.price_tolerance_bp, d.qty_tolerance_bp, d.description_similarity_min) == (200, 0, 80)
    s = MatchSettings.from_tenant(
        {"match_price_tolerance_bp": 50, "match_description_similarity_min": 70}
    )
    assert s.price_tolerance_bp == 50 and s.description_similarity_min == 70


@pytest.mark.parametrize(
    "raw",
    [
        {"match_price_tolerance_bp": -1},
        {"match_price_tolerance_bp": True},
        {"match_price_tolerance_bp": "5"},
        {"match_description_similarity_min": 101},
        {"match_qty_tolerance_bp": 10**9},
    ],
)
def test_bad_tenant_settings_fall_back_to_defaults(raw: dict[str, object]) -> None:
    assert MatchSettings.from_tenant(raw) == MatchSettings()


def test_two_po_lines_with_the_same_description_are_not_guessed() -> None:
    twins = (
        po_line(1, None, "Cable tie pack 100", 10, 1000),
        po_line(2, None, "Cable tie pack 100", 4, 3000),
    )
    lines = (iline(1, None, "Cable tie pack 100", 7, 1100),)
    r = run(inv(lines=lines, subtotal=7700), pos=[replace(PO, lines=twins)])
    assert r.line_matches == ()


# ---- credit notes, duplicate SKUs, SKU conflicts, bad numbers -------------------------------


def test_billed_minor_is_what_later_invoices_will_count() -> None:
    assert run().billed_minor == 20000
    assert run(inv(po_number="PO-0", subtotal=5)).billed_minor is None  # no PO found


@pytest.mark.parametrize(
    "invoice",
    [
        inv(subtotal=-5000),
        inv(lines=(iline(1, "A-100", None, -3, 1000), CLEAN_LINES[1]), subtotal=20000),
    ],
)
def test_credit_notes_are_left_to_a_person_and_never_reduce_billing(invoice: MatchInvoice) -> None:
    r = run(invoice)
    assert r.line_matches == () and r.billed_minor is None and not r.passed
    for code in (
        MatchCode.PRICE_VARIANCE,
        MatchCode.QTY_VARIANCE,
        MatchCode.RECEIPT_MISSING,
        MatchCode.QTY_NOT_RECEIVED,
        MatchCode.PO_OVERBILLED,
    ):
        assert out(r, code) is Outcome.SKIPPED
        assert details(r, code)["reason"] == "CREDIT_NOTE"


def test_a_sku_shared_by_two_po_lines_is_not_paired_by_sku() -> None:
    same = (
        po_line(1, "X-1", "Alpha bracket", 10, 1000),
        po_line(2, "X-1", "Beta bracket", 10, 1500),
    )
    r = run(
        inv(lines=(iline(1, "X-1", "Beta bracket", 10, 1500),), subtotal=15000),
        pos=[replace(PO, lines=same)],
    )
    assert [(m.po_line_id, m.method) for m in r.line_matches] == [("pl2", "description")]


def test_a_conflicting_sku_is_not_overridden_by_a_matching_description() -> None:
    lines = (iline(1, "Q-7", "Blue widget, box of 10", 10, 1000), CLEAN_LINES[1])
    r = run(inv(lines=lines))
    assert [m.invoice_line_no for m in r.line_matches] == [2]
    assert out(r, MatchCode.QTY_VARIANCE) is Outcome.FAIL
    assert details(r, MatchCode.QTY_VARIANCE)["findings"][0]["kind"] == "LINE_NOT_ON_PO"  # type: ignore[index]


def test_a_conflicting_sku_is_not_overridden_by_a_matching_amount() -> None:
    lines = (iline(1, "Z-9", "zzz", 10, 1000), CLEAN_LINES[1])
    assert [m.invoice_line_no for m in run(inv(lines=lines)).line_matches] == [2]


def test_a_quantity_that_is_not_a_number_is_unreadable_not_a_crash() -> None:
    bad = replace(CLEAN_LINES[0], qty=D("NaN"))
    r = run(inv(lines=(bad, CLEAN_LINES[1])))
    assert out(r, MatchCode.QTY_VARIANCE) is Outcome.SKIPPED
    assert details(r, MatchCode.QTY_VARIANCE)["reason"] == "UNREADABLE_LINE"
    assert not r.passed
