"""Accounting handoff rules (Project B): who may be handed off, what a draft bill is built from,
which contact it goes to, what to do when the provider misbehaves. All pure: no provider, no DB."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from intake.core.accounting import (
    EXPLANATIONS,
    HANDOFF_TRANSITIONS,
    STALE_AFTER_S,
    BillDraft,
    ContactCandidate,
    Failure,
    InvoiceFacts,
    LineFacts,
    Next,
    Problem,
    ReturnedBill,
    decide_after_failure,
    explain,
    handoff_key,
    handoff_problem,
    is_stale,
    map_invoice,
    match_contact,
    verify_created,
)
from intake.core.statuses import HandoffStatus, InvoiceStatus

TAX_TYPES = {1000: "INPUT", 0: "EXEMPTEXPENSES"}


def line(**kw: object) -> LineFacts:
    base: dict[str, object] = dict(
        description="Printer paper", qty=Decimal("2"), unit_price_minor=5000,
        amount_minor=10000, tax_rate=Decimal("10"),
    )  # fmt: skip
    base.update(kw)
    return LineFacts(**base)  # type: ignore[arg-type]


def facts(**kw: object) -> InvoiceFacts:
    base: dict[str, object] = dict(
        invoice_number="INV-001", invoice_date=date(2026, 3, 1), due_date=date(2026, 3, 31),
        currency="AUD", subtotal_minor=10000, tax_minor=1000, total_minor=11000,
        lines=(line(),),
    )  # fmt: skip
    base.update(kw)
    return InvoiceFacts(**base)  # type: ignore[arg-type]


# ---- who may be handed off ------------------------------------------------------------------


def test_only_an_approved_invoice_with_no_handoff_is_eligible() -> None:
    assert handoff_problem(InvoiceStatus.APPROVED, None) is None


@pytest.mark.parametrize(
    "status",
    [s for s in InvoiceStatus if s is not InvoiceStatus.APPROVED],
)
def test_every_other_invoice_status_is_refused(status: InvoiceStatus) -> None:
    assert handoff_problem(status, None) is Problem.NOT_APPROVED


def test_a_failed_handoff_may_be_started_again() -> None:
    assert handoff_problem(InvoiceStatus.APPROVED, HandoffStatus.FAILED) is None


@pytest.mark.parametrize(
    ("existing", "problem"),
    [
        (HandoffStatus.SUCCEEDED, Problem.ALREADY_HANDED_OFF),
        (HandoffStatus.IN_PROGRESS, Problem.HANDOFF_IN_PROGRESS),
        (HandoffStatus.PENDING, Problem.HANDOFF_IN_PROGRESS),
        (HandoffStatus.UNKNOWN, Problem.OUTCOME_UNKNOWN),
    ],
)
def test_an_existing_handoff_blocks_a_second_write(
    existing: HandoffStatus, problem: Problem
) -> None:
    assert handoff_problem(InvoiceStatus.APPROVED, existing) is problem


def test_unknown_outcome_never_goes_straight_back_to_in_progress() -> None:
    assert HandoffStatus.IN_PROGRESS not in HANDOFF_TRANSITIONS[HandoffStatus.UNKNOWN]
    assert HANDOFF_TRANSITIONS[HandoffStatus.SUCCEEDED] == frozenset()


# ---- the idempotency key --------------------------------------------------------------------

INV = uuid.UUID("11111111-1111-4111-8111-111111111111")
TEN = uuid.UUID("22222222-2222-4222-8222-222222222222")


def test_handoff_key_is_stable_and_within_the_provider_limit() -> None:
    key = handoff_key(TEN, "xero", "org-1", INV)
    assert key == handoff_key(TEN, "xero", "org-1", INV)
    assert len(key) <= 128


@pytest.mark.parametrize(
    "other",
    [
        (uuid.uuid4(), "xero", "org-1", INV),
        (TEN, "myob", "org-1", INV),
        (TEN, "xero", "org-2", INV),
        (TEN, "xero", "org-1", uuid.uuid4()),
    ],
)
def test_handoff_key_changes_with_every_part(other: tuple[uuid.UUID, str, str, uuid.UUID]) -> None:
    assert handoff_key(*other) != handoff_key(TEN, "xero", "org-1", INV)


# ---- mapping an invoice to a draft bill -----------------------------------------------------


def test_a_good_invoice_maps_to_a_draft_bill_without_changing_a_cent() -> None:
    result = map_invoice(facts(), "contact-1", TAX_TYPES)
    assert result.problems == ()
    bill = result.bill
    assert isinstance(bill, BillDraft)
    assert (bill.contact_id, bill.invoice_number, bill.currency) == ("contact-1", "INV-001", "AUD")
    assert (bill.subtotal_minor, bill.tax_minor, bill.total_minor) == (10000, 1000, 11000)
    assert bill.due_date == date(2026, 3, 31)
    [ln] = bill.lines
    assert (ln.description, ln.quantity, ln.unit_amount_minor, ln.amount_minor) == (
        "Printer paper", Decimal("2"), 5000, 10000,
    )  # fmt: skip
    assert ln.tax_type == "INPUT"
    assert ln.account_code is None  # no approved mapping: left blank, never guessed


def test_a_line_whose_quantity_times_price_is_not_its_amount_sends_the_amount_only() -> None:
    bill = map_invoice(facts(lines=(line(unit_price_minor=4999),)), "c", TAX_TYPES).bill
    assert bill is not None
    assert (bill.lines[0].quantity, bill.lines[0].unit_amount_minor) == (None, None)
    assert bill.lines[0].amount_minor == 10000


def test_a_missing_due_date_stays_missing() -> None:
    bill = map_invoice(facts(due_date=None), "c", TAX_TYPES).bill
    assert bill is not None and bill.due_date is None


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"invoice_number": None}, Problem.INVOICE_NUMBER_MISSING),
        ({"invoice_number": "  "}, Problem.INVOICE_NUMBER_MISSING),
        ({"invoice_date": None}, Problem.INVOICE_DATE_MISSING),
        ({"currency": None}, Problem.CURRENCY_UNSUPPORTED),
        ({"currency": "XYZ"}, Problem.CURRENCY_UNSUPPORTED),
        ({"lines": ()}, Problem.NO_LINES),
        ({"total_minor": None}, Problem.TOTALS_MISSING),
        ({"subtotal_minor": None}, Problem.TOTALS_MISSING),
        ({"tax_minor": None}, Problem.TOTALS_MISSING),
        ({"total_minor": 11001}, Problem.TOTALS_MISMATCH),
        ({"subtotal_minor": 9999, "total_minor": 10999}, Problem.TOTALS_MISMATCH),
        ({"subtotal_minor": 0, "tax_minor": 0, "total_minor": 0}, Problem.NOT_A_BILL),
        (
            {"subtotal_minor": -10000, "tax_minor": -1000, "total_minor": -11000,
             "lines": (line(amount_minor=-10000, unit_price_minor=-5000),)},
            Problem.NOT_A_BILL,
        ),
    ],
)  # fmt: skip
def test_an_invoice_that_cannot_be_mapped_safely_is_refused_before_any_write(
    change: dict[str, object], problem: Problem
) -> None:
    result = map_invoice(facts(**change), "c", TAX_TYPES)
    assert result.bill is None
    assert problem in result.problems


@pytest.mark.parametrize(
    ("ln", "problem"),
    [
        (line(description=None), Problem.LINE_DESCRIPTION_MISSING),
        (line(description=" "), Problem.LINE_DESCRIPTION_MISSING),
        (line(amount_minor=None), Problem.LINE_AMOUNT_MISSING),
        (line(tax_rate=None), Problem.TAX_MAPPING_MISSING),
        (line(tax_rate=Decimal("7")), Problem.TAX_MAPPING_MISSING),  # not in the approved mapping
        (line(tax_rate=Decimal("10.5")), Problem.TAX_MAPPING_MISSING),
    ],
)
def test_a_bad_line_is_refused(ln: LineFacts, problem: Problem) -> None:
    result = map_invoice(facts(lines=(ln,)), "c", TAX_TYPES)
    assert result.bill is None
    assert problem in result.problems


def test_lines_that_do_not_add_up_to_the_subtotal_are_refused() -> None:
    result = map_invoice(
        facts(lines=(line(amount_minor=9000, unit_price_minor=4500),)), "c", {1000: "INPUT"}
    )
    assert Problem.TOTALS_MISMATCH in result.problems


def test_tax_that_xero_would_calculate_differently_is_refused_not_adjusted() -> None:
    result = map_invoice(facts(tax_minor=1001, total_minor=11001), "c", TAX_TYPES)
    assert result.bill is None
    assert Problem.TAX_MISMATCH in result.problems


def test_tax_is_rounded_half_up_per_line_like_the_validation_stage() -> None:
    lines = (
        line(qty=Decimal("1"), unit_price_minor=105, amount_minor=105),  # 10.5c -> 11c
        line(qty=Decimal("1"), unit_price_minor=105, amount_minor=105),
    )
    result = map_invoice(
        facts(lines=lines, subtotal_minor=210, tax_minor=22, total_minor=232), "c", TAX_TYPES
    )
    assert result.problems == ()


def test_zero_rated_lines_need_an_explicit_mapping_too() -> None:
    zero = line(tax_rate=Decimal("0"))
    ok = map_invoice(facts(lines=(zero,), tax_minor=0, total_minor=10000), "c", TAX_TYPES)
    assert ok.bill is not None and ok.bill.lines[0].tax_type == "EXEMPTEXPENSES"
    no = map_invoice(facts(lines=(zero,), tax_minor=0, total_minor=10000), "c", {1000: "INPUT"})
    assert Problem.TAX_MAPPING_MISSING in no.problems


def test_every_problem_is_reported_not_just_the_first() -> None:
    result = map_invoice(facts(invoice_number=None, invoice_date=None), "c", TAX_TYPES)
    assert {Problem.INVOICE_NUMBER_MISSING, Problem.INVOICE_DATE_MISSING} <= set(result.problems)


# ---- choosing the supplier's contact --------------------------------------------------------


def cand(cid: str, name: str, tax: str | None = None, active: bool = True) -> ContactCandidate:
    return ContactCandidate(contact_id=cid, name=name, tax_number=tax, active=active)


def test_a_stored_link_wins() -> None:
    d = match_contact("Acme Pty Ltd", "51 824 753 556", "c-9", [cand("c-9", "ACME")])
    assert (d.contact_id, d.matched_by, d.problem) == ("c-9", "link", None)


def test_a_stored_link_to_a_contact_that_is_gone_needs_a_person() -> None:
    d = match_contact("Acme", None, "c-9", [cand("c-1", "Acme")])
    assert (d.contact_id, d.problem) == (None, Problem.CONTACT_NOT_FOUND)


def test_a_stored_link_whose_tax_number_disagrees_is_a_conflict() -> None:
    d = match_contact("Acme", "51824753556", "c-9", [cand("c-9", "Acme", "53004085616")])
    assert d.problem is Problem.CONTACT_CONFLICT


def test_an_abn_match_ignores_spacing() -> None:
    d = match_contact("Acme", "51 824 753 556", None, [cand("c-1", "Other", "51824753556")])
    assert (d.contact_id, d.matched_by) == ("c-1", "tax_id")


def test_two_contacts_with_the_same_abn_are_ambiguous() -> None:
    cands = [cand("c-1", "A", "51824753556"), cand("c-2", "B", "51824753556")]
    d = match_contact("Acme", "51824753556", None, cands)
    assert (d.contact_id, d.problem) == (None, Problem.CONTACT_AMBIGUOUS)


def test_archived_contacts_are_never_chosen() -> None:
    d = match_contact("Acme", "51824753556", None, [cand("c-1", "Acme", "51824753556", False)])
    assert d.problem is Problem.CONTACT_NOT_FOUND


def test_an_exact_name_matches_when_no_tax_number_conflicts() -> None:
    d = match_contact("Acme Pty. Ltd.", None, None, [cand("c-1", "ACME PTY LTD")])
    assert (d.contact_id, d.matched_by) == ("c-1", "name")


def test_a_similar_name_is_not_enough() -> None:
    d = match_contact("Acme Pty Ltd", None, None, [cand("c-1", "Acme Pty Ltd Holdings")])
    assert d.problem is Problem.CONTACT_NOT_FOUND


def test_two_contacts_with_the_same_name_are_ambiguous() -> None:
    d = match_contact("Acme", None, None, [cand("c-1", "Acme"), cand("c-2", "acme")])
    assert d.problem is Problem.CONTACT_AMBIGUOUS


def test_a_name_match_with_a_different_tax_number_is_a_conflict() -> None:
    d = match_contact("Acme", "51824753556", None, [cand("c-1", "Acme", "53004085616")])
    assert d.problem is Problem.CONTACT_CONFLICT


def test_no_candidates_means_not_found() -> None:
    assert match_contact("Acme", None, None, []).problem is Problem.CONTACT_NOT_FOUND


def test_a_name_made_only_of_punctuation_matches_nobody() -> None:
    d = match_contact("...", None, None, [cand("c-1", "---")])
    assert d.problem is Problem.CONTACT_NOT_FOUND


# ---- checking what the provider says it created ---------------------------------------------


def draft() -> BillDraft:
    bill = map_invoice(facts(), "contact-1", TAX_TYPES).bill
    assert bill is not None
    return bill


def returned(**kw: object) -> ReturnedBill:
    base: dict[str, object] = dict(
        bill_id="b-1", status="DRAFT", contact_id="contact-1", currency="AUD",
        subtotal_minor=10000, tax_minor=1000, total_minor=11000,
    )  # fmt: skip
    base.update(kw)
    return ReturnedBill(**base)  # type: ignore[arg-type]


def test_a_draft_bill_with_the_same_amounts_is_verified() -> None:
    assert verify_created(draft(), returned()) is None


@pytest.mark.parametrize("status", ["AUTHORISED", "SUBMITTED", "PAID", "VOIDED", "", "draft"])
def test_any_status_but_draft_is_critical(status: str) -> None:
    assert verify_created(draft(), returned(status=status)) is Problem.UNEXPECTED_BILL_STATUS


@pytest.mark.parametrize(
    "change",
    [
        {"total_minor": 11001},
        {"tax_minor": 999},
        {"subtotal_minor": 1},
        {"contact_id": "someone-else"},
        {"currency": "NZD"},
        {"bill_id": ""},
    ],
)
def test_a_bill_that_differs_from_what_was_sent_is_not_trusted(change: dict[str, object]) -> None:
    assert verify_created(draft(), returned(**change)) is Problem.RESPONSE_MISMATCH


# ---- what to do when a call fails -----------------------------------------------------------


@pytest.mark.parametrize("failure", [Failure.CONNECT_FAILED, Failure.RATE_LIMITED])
def test_a_request_the_provider_never_processed_may_be_retried_a_few_times(
    failure: Failure,
) -> None:
    d = decide_after_failure(failure, attempt=1, retry_after_s=None)
    assert d.next is Next.RETRY and d.delay_s > 0
    assert decide_after_failure(failure, attempt=4, retry_after_s=None).next is Next.FAIL


def test_retry_after_is_obeyed_and_capped() -> None:
    assert decide_after_failure(Failure.RATE_LIMITED, 1, retry_after_s=30).delay_s == 30
    assert decide_after_failure(Failure.RATE_LIMITED, 1, retry_after_s=100000).next is Next.FAIL


@pytest.mark.parametrize(
    "failure",
    [Failure.TIMEOUT, Failure.SERVER_ERROR, Failure.CONNECTION_LOST, Failure.BAD_RESPONSE],
)
def test_a_failure_after_the_request_may_have_been_sent_is_never_retried_blindly(
    failure: Failure,
) -> None:
    d = decide_after_failure(failure, attempt=1, retry_after_s=None)
    assert d.next is Next.UNKNOWN
    assert d.manual_action is True


@pytest.mark.parametrize(
    "failure",
    [Failure.UNAUTHORISED, Failure.FORBIDDEN, Failure.REJECTED],
)
def test_authentication_permission_and_validation_failures_are_not_retried(
    failure: Failure,
) -> None:
    d = decide_after_failure(failure, attempt=1, retry_after_s=None)
    assert d.next is Next.FAIL and d.manual_action is True


def test_a_wrong_status_in_the_response_is_critical() -> None:
    assert decide_after_failure(Failure.UNEXPECTED_STATUS, 1, None).next is Next.CRITICAL


# ---- a handoff left running by a crashed worker ---------------------------------------------


def test_a_handoff_still_running_long_after_it_started_is_stale() -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    assert not is_stale(now - timedelta(seconds=STALE_AFTER_S - 1), now)
    assert is_stale(now - timedelta(seconds=STALE_AFTER_S), now)


# ---- plain-language explanations ------------------------------------------------------------


def test_every_problem_and_failure_has_an_explanation() -> None:
    for code in [*Problem, *Failure]:
        assert EXPLANATIONS[code.value].endswith(".")


def test_success_is_only_claimed_for_succeeded_and_says_a_person_approves() -> None:
    text = explain(HandoffStatus.SUCCEEDED, None)
    assert "draft" in text and "approve" in text
    for status in (HandoffStatus.FAILED, HandoffStatus.UNKNOWN):
        assert "created." not in explain(status, "timeout").split("may")[0]
        assert not explain(status, "timeout").startswith("A draft bill was created")


def test_explanations_for_running_unknown_and_unexpected_codes() -> None:
    assert "Sending" in explain(HandoffStatus.IN_PROGRESS, None)
    assert explain(HandoffStatus.FAILED, None) == "The handoff did not complete."
    assert explain(HandoffStatus.FAILED, "SOMETHING_NEW") == "The handoff did not complete."
    assert "unexpected" in explain(HandoffStatus.FAILED, "INTERNAL_ERROR:ValueError").lower()
    assert explain(HandoffStatus.FAILED, "CONTACT_AMBIGUOUS") == EXPLANATIONS["CONTACT_AMBIGUOUS"]
