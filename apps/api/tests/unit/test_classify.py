from decimal import Decimal

import pytest

from intake.core.classify import (
    REASON_TEXT,
    CheckRow,
    ClassifyContext,
    ExceptionDraft,
    WeakField,
    classify,
)
from intake.core.exceptions import SPECS, ExceptionCode, Severity
from intake.core.validate import Outcome

C = ExceptionCode
D = Decimal


def row(code: str, outcome: Outcome = Outcome.FAIL, **details: object) -> CheckRow:
    return CheckRow(code, outcome, dict(details))


def ctx(**kw: object) -> ClassifyContext:
    base: dict[str, object] = {
        "currency": "USD",
        "total_minor": 100_000,
        "supplier_name": "Acme Supply Co.",
        "approval_limit_minor": 1_000_000,
        "weak_fields": (),
    }
    return ClassifyContext(**{**base, **kw})  # type: ignore[arg-type]


def one(check: CheckRow, **kw: object) -> ExceptionDraft:
    drafts = classify([check], ctx(**kw))
    assert len(drafts) == 1, drafts
    return drafts[0]


def test_passing_checks_raise_nothing() -> None:
    passes = [row(c.value, Outcome.PASS) for c in C]
    assert classify(passes, ctx()) == []


def test_a_draft_carries_the_code_severity_fix_and_the_evidence() -> None:
    d = one(row("RECEIPT_MISSING", po_number="PO-7781"))
    assert (d.code, d.severity, d.unchecked) == (C.RECEIPT_MISSING, Severity.REVIEW, False)
    assert d.suggested_fix == SPECS[C.RECEIPT_MISSING].suggested_fix
    assert d.explanation == "Nothing has been received yet for PO-7781."
    assert d.evidence == {"po_number": "PO-7781"}


# ---- validation checks ------------------------------------------------------------------------


def test_line_math_names_every_bad_line() -> None:
    bad = [
        {"line_no": 3, "quantity": "12", "unit_price_minor": 4500,
         "expected_minor": 54000, "amount_minor": 50400},
        {"line_no": 5, "quantity": "2", "unit_price_minor": 1000,
         "expected_minor": 2000, "amount_minor": 2500},
    ]  # fmt: skip
    d = one(row("LINE_MATH_MISMATCH", checked=5, lines=bad, unchecked_lines=[]))
    assert d.explanation == (
        "Line 3: 12 × USD 45.00 should be USD 540.00, but the invoice says USD 504.00. "
        "Line 5: 2 × USD 10.00 should be USD 20.00, but the invoice says USD 25.00."
    )


def test_total_mismatch_says_which_comparison_failed() -> None:
    lines_vs_subtotal = {"name": "LINES_SUM_TO_SUBTOTAL", "ok": False,
                         "expected_minor": 214000, "actual_minor": 241000}  # fmt: skip
    plus_tax = {"name": "SUBTOTAL_PLUS_TAX_IS_TOTAL", "ok": False,
                "expected_minor": 250000, "actual_minor": 260000}  # fmt: skip
    fine = {"name": "SUBTOTAL_PLUS_TAX_IS_TOTAL", "ok": True,
            "expected_minor": 1, "actual_minor": 1}  # fmt: skip
    d = one(row("TOTAL_MISMATCH", checks=[lines_vs_subtotal, fine, plus_tax]))
    assert d.explanation == (
        "The lines add up to USD 2,140.00, but the invoice subtotal is USD 2,410.00. "
        "The subtotal plus tax comes to USD 2,500.00, but the invoice total is USD 2,600.00."
    )


@pytest.mark.parametrize(
    ("extra", "basis"),
    [
        ({"source": "supplier", "rate_bp": 1500}, "15% of USD 2,140.00"),
        ({"source": "supplier", "rate_bp": 1550}, "15.5% of USD 2,140.00"),
        ({"source": "lines", "rate_bp": None}, "the tax on each line"),
    ],
)
def test_tax_mismatch_states_the_rate_or_the_line_basis(
    extra: dict[str, object], basis: str
) -> None:
    d = one(
        row(
            "TAX_MISMATCH", expected_minor=32100, actual_minor=31800, subtotal_minor=214000, **extra
        )
    )
    assert d.explanation == f"Tax is USD 318.00, but {basis} is USD 321.00."


def test_invalid_date_explains_each_reason_with_the_dates() -> None:
    reasons = [
        {"code": "FUTURE_INVOICE_DATE", "invoice_date": "2026-12-01"},
        {"code": "INVOICE_TOO_OLD", "invoice_date": "2025-01-01", "max_age_days": 365},
        {"code": "DUE_BEFORE_INVOICE", "invoice_date": "2026-06-01", "due_date": "2026-05-01"},
    ]
    d = one(row("INVALID_DATE", reasons=reasons, today="2026-09-25"))
    assert d.explanation == (
        "The invoice date is invalid: it is in the future (2026-12-01; today is 2026-09-25). "
        "The invoice date is invalid: it is more than 365 days old (2025-01-01). "
        "The due date is invalid: it is before the invoice date "
        "(due 2026-05-01, invoice dated 2026-06-01)."
    )


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        ({"invoice_currency": "EUR", "supplier_currency": "USD", "printed": "€"},
         "The invoice is in EUR, but this supplier usually invoices in USD."),
        ({"invoice_currency": None, "supplier_currency": "USD", "printed": "GBP"},
         "The invoice is in GBP, but this supplier usually invoices in USD."),
        ({"invoice_currency": None, "supplier_currency": "USD", "printed": None},
         "The invoice is in an unknown currency, but this supplier usually invoices in USD."),
    ],
)  # fmt: skip
def test_currency_mismatch_says_both_currencies(details: dict[str, object], expected: str) -> None:
    assert one(row("CURRENCY_MISMATCH", **details)).explanation == expected


def test_unknown_supplier_offers_the_closest_match_when_there_is_one() -> None:
    rest = {"suggested": False, "conflict": False}
    d = one(row("UNKNOWN_SUPPLIER", printed_name="Acme Supplies Ltd", printed_tax_id=None,
                closest_name="ACME Supply Co.", closest_score=82, **rest))  # fmt: skip
    assert d.explanation == (
        "'Acme Supplies Ltd' doesn't match any known supplier. "
        "Closest match: 'ACME Supply Co.' (82%)."
    )
    bare = one(row("UNKNOWN_SUPPLIER", printed_name="Zed Ltd", printed_tax_id=None,
                   closest_name=None, closest_score=0, **rest))  # fmt: skip
    assert bare.explanation == "'Zed Ltd' doesn't match any known supplier."
    no_name = one(row("UNKNOWN_SUPPLIER", printed_name=None, printed_tax_id="12-345",
                      closest_name=None, closest_score=0, **rest))  # fmt: skip
    assert "12-345" in no_name.explanation


def test_bank_change_blocks_and_names_the_supplier() -> None:
    d = one(row("BANK_DETAILS_CHANGED", supplier_id="s1", bank_on_invoice=True))
    assert d.severity is Severity.BLOCK
    assert d.explanation == (
        "The bank account on this invoice is different from the one on file for Acme Supply Co.."
    )
    assert (
        "this supplier"
        in one(row("BANK_DETAILS_CHANGED", supplier_id="s1"), supplier_name=None).explanation
    )


# ---- duplicate and match checks ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "match"),
    [("hard", "the same invoice number"), ("soft", "the same amount and a very similar number")],
)
def test_duplicate_names_the_earlier_invoice(kind: str, match: str) -> None:
    d = one(row("POSSIBLE_DUPLICATE", kind=kind, existing_invoice_id="i9",
                existing_invoice_number="INV-1043", days_apart=4, similarity=100.0,
                compared=3))  # fmt: skip
    assert d.severity is Severity.BLOCK
    assert d.explanation == (
        f"This looks like invoice INV-1043 from the same supplier: {match}, "
        "with invoice dates 4 days apart."
    )
    assert d.evidence["existing_invoice_id"] == "i9"


def test_duplicate_with_missing_number_and_days_still_reads_well() -> None:
    d = one(row("POSSIBLE_DUPLICATE", kind="hard", existing_invoice_id="i9",
                existing_invoice_number=None, days_apart=None))  # fmt: skip
    assert "an earlier invoice" in d.explanation and "an unknown number of days" in d.explanation


def test_no_po_and_po_not_found() -> None:
    assert one(row("NO_PO", reason="NO_OPEN_PO_WITH_MATCHING_TOTAL")).explanation == (
        "This invoice has no PO number, and no open PO for this supplier matches the amount."
    )
    cases = {
        "NOT_IN_SYSTEM": "PO-94281 isn't in the system.",
        "PO_OF_OTHER_SUPPLIER": "PO-94281 belongs to a different supplier.",
        "PO_NOT_OPEN": "PO-94281 is not open (its status is closed).",
    }
    for reason, text in cases.items():
        d = one(row("PO_NOT_FOUND", po_number="PO-94281", reason=reason, status="closed"))
        assert d.explanation == text


def test_price_variance_shows_signed_percent_and_limit() -> None:
    findings = [
        {"line": 2, "invoice_unit_price_minor": 4800, "po_unit_price_minor": 4500,
         "variance_bp": 666, "limit_bp": 200},
        {"line": 4, "invoice_unit_price_minor": 880, "po_unit_price_minor": 1000,
         "variance_bp": 1200, "limit_bp": 200},
        {"line": 5, "invoice_unit_price_minor": 100, "po_unit_price_minor": 0,
         "variance_bp": None, "limit_bp": 150},
    ]  # fmt: skip
    d = one(row("PRICE_VARIANCE", findings=findings))
    assert d.explanation == (
        "Line 2 is billed at USD 48.00 per unit, but the PO says USD 45.00 (+6.7%, limit 2%). "
        "Line 4 is billed at USD 8.80 per unit, but the PO says USD 10.00 (-12.0%, limit 2%). "
        "Line 5 is billed at USD 1.00 per unit, but the PO says USD 0.00 "
        "(no percentage, the PO price is zero, limit 1.5%)."
    )


def test_quantity_variance_counts_earlier_billing_and_unmatched_lines() -> None:
    findings = [
        {"line": 1, "kind": "OVER_PO_QTY", "po_qty": "100", "billed_qty": "120",
         "billed_before_qty": "0"},
        {"line": 2, "kind": "OVER_PO_QTY", "po_qty": "10", "billed_qty": "4",
         "billed_before_qty": "7"},
        {"line": 3, "kind": "LINE_NOT_ON_PO"},
    ]  # fmt: skip
    d = one(row("QTY_VARIANCE", findings=findings))
    assert d.explanation == (
        "Line 1 bills 120 units, but the PO line is for 100. "
        "Line 2 bills 4 units (on top of 7 already billed on earlier invoices), "
        "but the PO line is for 10. "
        "Line 3 could not be matched to any line on the PO."
    )


def test_quantity_not_received_states_billed_and_received() -> None:
    findings = [
        {"line": 3, "billed_qty": "24", "received_qty": "17", "billed_before_qty": "0"},
        {"line": 4, "billed_qty": "2", "received_qty": "6", "billed_before_qty": "6"},
    ]
    d = one(row("QTY_NOT_RECEIVED", findings=findings))
    assert d.explanation == (
        "Line 3 bills 24 units, but only 17 have been received. "
        "Line 4 bills 2 units (on top of 6 already billed on earlier invoices), "
        "but only 6 have been received."
    )


def test_po_overbilled_blocks_and_uses_the_po_currency() -> None:
    d = one(row("PO_OVERBILLED", po_number="PO-7781", po_total_minor=980000,
                billed_before_minor=500000, invoice_subtotal_minor=545000,
                billed_total_minor=1045000, over_minor=65000, currency="EUR"),
            currency="USD")  # fmt: skip
    assert d.severity is Severity.BLOCK
    assert d.explanation == (
        "Invoices against PO-7781 now total EUR 10,450.00, "
        "which is more than the PO total of EUR 9,800.00."
    )


# ---- checks that are not stored as check results ---------------------------------------------


def test_weak_critical_fields_are_one_exception_listing_each() -> None:
    weak = (WeakField("total", D("0.62"), D("0.8")), WeakField("invoice_date", None, D("0.8")))
    d = classify([], ctx(weak_fields=weak))[0]
    assert d.code is C.LOW_CONFIDENCE_FIELD and d.severity is Severity.REVIEW
    assert d.explanation == (
        "We're not sure about the total: its confidence is 62%, below the 80% minimum. "
        "We're not sure about the invoice date: it could not be read."
    )


def test_a_total_above_the_limit_raises_above_approval_limit() -> None:
    d = classify([], ctx(total_minor=2_500_000))[0]
    assert d.code is C.ABOVE_APPROVAL_LIMIT
    assert (
        d.explanation
        == "The total of USD 25,000.00 is above the USD 10,000.00 auto-approval limit."
    )
    assert classify([], ctx(total_minor=1_000_000)) == []
    assert classify([], ctx(total_minor=None)) == []
    assert classify([], ctx(approval_limit_minor=None)) == []


# ---- could not be checked ---------------------------------------------------------------------


def test_a_skipped_check_is_raised_as_could_not_be_checked() -> None:
    d = one(row("TAX_MISMATCH", Outcome.SKIPPED, reason="NO_RATE_TO_COMPARE"))
    assert (d.code, d.severity, d.unchecked) == (C.TAX_MISMATCH, Severity.REVIEW, True)
    assert d.explanation.startswith("The tax could not be checked (")
    assert REASON_TEXT["NO_RATE_TO_COMPARE"] in d.explanation


def test_a_skipped_block_check_is_review_not_block() -> None:
    d = one(row("POSSIBLE_DUPLICATE", Outcome.SKIPPED, reason="INCOMPLETE_FOR_SOFT_MATCH"))
    assert d.severity is Severity.REVIEW and d.unchecked


def test_an_unknown_reason_still_raises_an_exception() -> None:
    d = one(row("TAX_MISMATCH", Outcome.SKIPPED, reason="SOMETHING_NEW"))
    assert d.unchecked and "SOMETHING_NEW" in d.explanation
    assert one(row("TAX_MISMATCH", Outcome.SKIPPED)).unchecked


def test_checks_skipped_only_because_of_another_problem_are_not_repeated() -> None:
    drafts = classify(
        [
            row("UNKNOWN_SUPPLIER", printed_name="Zed", closest_name=None, closest_score=0),
            row("CURRENCY_MISMATCH", Outcome.SKIPPED, reason="UNKNOWN_SUPPLIER"),
            row("BANK_DETAILS_CHANGED", Outcome.SKIPPED, reason="UNKNOWN_SUPPLIER"),
            row("PO_NOT_FOUND", po_number="PO-1", reason="NOT_IN_SYSTEM"),
            row("PRICE_VARIANCE", Outcome.SKIPPED, reason="NO_PO"),
            row("PO_OVERBILLED", Outcome.SKIPPED, reason="NO_PO"),
        ],
        ctx(),
    )
    assert {d.code for d in drafts} == {C.UNKNOWN_SUPPLIER, C.PO_NOT_FOUND}


def test_a_derived_skip_is_kept_when_the_root_problem_was_not_raised() -> None:
    d = one(row("CURRENCY_MISMATCH", Outcome.SKIPPED, reason="UNKNOWN_SUPPLIER"))
    assert d.unchecked  # nothing else explains why it was skipped, so a person must see it


def test_drafts_are_ordered_block_first_then_by_taxonomy() -> None:
    drafts = classify(
        [
            row("PRICE_VARIANCE", findings=[]),
            row("BANK_DETAILS_CHANGED", supplier_id="s"),
            row("RECEIPT_MISSING", po_number="P"),
            row("LINE_MATH_MISMATCH", lines=[]),
        ],
        ctx(),
    )
    assert [d.code for d in drafts] == [
        C.BANK_DETAILS_CHANGED,
        C.LINE_MATH_MISMATCH,
        C.PRICE_VARIANCE,
        C.RECEIPT_MISSING,
    ]


# ---- robustness -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        c.value
        for c in C
        if c.value not in ("LOW_CONFIDENCE_FIELD", "ABOVE_APPROVAL_LIMIT", "UNREADABLE_DOCUMENT")
    ],
)
def test_a_failed_check_with_no_details_never_crashes_and_is_never_dropped(code: str) -> None:
    d = one(row(code))
    assert d.code.value == code and d.explanation and "{" not in d.explanation
    assert d.severity in (Severity.REVIEW, Severity.BLOCK)


def test_an_unknown_check_code_is_an_error_not_a_pass() -> None:
    with pytest.raises(ValueError):
        classify([row("MADE_UP")], ctx())


def test_amounts_without_a_known_currency_are_shown_without_a_code() -> None:
    d = one(row("TOTAL_MISMATCH", checks=[{"name": "LINES_SUM_TO_SUBTOTAL", "ok": False,
                                            "expected_minor": 214000, "actual_minor": 241000}]),
            currency=None)  # fmt: skip
    assert d.explanation == "The lines add up to 2,140.00, but the invoice subtotal is 2,410.00."


def test_every_reason_the_checks_can_give_has_words() -> None:
    known = [
        "MISSING_AMOUNTS", "NO_RATE_TO_COMPARE", "NO_INVOICE_DATE", "UNKNOWN_SUPPLIER",
        "NOTHING_TO_MATCH", "BANK_NOT_READ", "NO_BANK_ON_FILE",
        "NO_SUPPLIER", "NO_INVOICE_NUMBER", "INCOMPLETE_FOR_SOFT_MATCH", "SUPPLIER_UNCERTAIN",
        "EARLIER_INVOICES_STILL_PENDING", "TOO_MANY_TO_COMPARE",
        "NO_PO", "CANNOT_INFER_PO", "PO_INFERRED", "SUPPLIER_UNKNOWN", "AMBIGUOUS_PO",
        "CREDIT_NOTE", "NO_LINES", "PO_HAS_NO_LINES", "NO_RECEIPT", "NO_MATCHED_LINES",
        "UNREADABLE_LINE", "CURRENCY_DIFFERS_FROM_PO", "CURRENCY_UNKNOWN", "NO_SUBTOTAL",
        "EARLIER_BILLING_UNKNOWN", "TOO_MANY_POS_TO_COMPARE",
    ]  # fmt: skip
    assert set(known) <= set(REASON_TEXT)
    assert all(text and text == text.strip() and "{" not in text for text in REASON_TEXT.values())


def test_a_missing_receipt_already_explains_the_skipped_received_quantity_check() -> None:
    drafts = classify(
        [
            row("RECEIPT_MISSING", po_number="PO-1"),
            row("QTY_NOT_RECEIVED", Outcome.SKIPPED, reason="NO_RECEIPT"),
        ],
        ctx(),
    )
    assert [d.code for d in drafts] == [C.RECEIPT_MISSING]


def test_a_credit_note_is_one_card_not_five() -> None:
    drafts = classify(
        [
            row(c, Outcome.SKIPPED, reason="CREDIT_NOTE")
            for c in ("PRICE_VARIANCE", "QTY_VARIANCE", "RECEIPT_MISSING", "PO_OVERBILLED")
        ],
        ctx(),
    )
    assert len(drafts) == 1 and drafts[0].unchecked
