from dataclasses import replace
from datetime import date

import pytest

from intake.core.dedupe import (
    RULE_VERSION,
    DedupeSettings,
    DuplicateKind,
    InvoiceRef,
    check_duplicate,
    number_key,
    number_similarity,
    supplier_key,
)
from intake.core.validate import Outcome

S = "sup-1"


def ref(
    id_: str,
    number: str | None = "INV-1043",
    day: date | None = date(2026, 5, 1),
    total: int | None = 50000,
    currency: str | None = "USD",
    supplier: str | None = S,
) -> InvoiceRef:
    return InvoiceRef(id_, supplier, number, day, total, currency)


NEW = ref("new")
ORIGINAL = ref("old")


def check(
    new: InvoiceRef = NEW,
    earlier: list[InvoiceRef] | None = None,
    settings: DedupeSettings | None = None,
):  # type: ignore[no-untyped-def]
    return check_duplicate(
        new, [ORIGINAL] if earlier is None else earlier, settings or DedupeSettings()
    )


# ---- keys ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("INV-1043", "INV1043"), ("inv 1043", "INV1043"), ("INV1043", "INV1043"),
        (" #1043 ", "1043"), ("TVS-2026-0675", "TVS20260675"), ("rfm-2026-0504", "RFM20260504"),
        ("Ré-12", "RÉ12"), ("---", ""), ("", ""),
    ],
)  # fmt: skip
def test_number_key_keeps_only_letters_and_digits_in_upper_case(raw: str, key: str) -> None:
    assert number_key(raw) == key


def test_supplier_key_prefers_the_linked_supplier_over_the_printed_name() -> None:
    assert supplier_key("abc-123", "Acme Ltd.") == "id:abc-123"
    assert supplier_key(None, "  ACME,  Ltd. ") == "name:acme ltd"
    assert supplier_key(None, None) is None
    assert supplier_key(None, "---") is None


def test_similarity_of_the_three_ways_to_write_a_number_is_high() -> None:
    assert number_similarity("INV-1043", "INV1043") == 100
    assert number_similarity("INV-1043", "1043") >= 85  # the digits agree
    assert number_similarity("INV1043", "1043") >= 85


def test_one_digit_off_is_still_close_and_different_numbers_are_not() -> None:
    assert number_similarity("KP-2026-0636", "KP-2026-0635") >= 85
    assert number_similarity("INV-1043", "INV-9999") < 85


def test_short_digit_strings_do_not_count_as_matching_digits() -> None:
    assert number_similarity("A-12", "B-12") < 85  # two digits are not evidence


# ---- hard duplicates -----------------------------------------------------------------------


def test_the_same_number_is_a_hard_duplicate() -> None:
    r = check()
    assert r.outcome is Outcome.FAIL and r.match is not None
    assert (r.match.kind, r.match.existing.id) == (DuplicateKind.HARD, "old")


@pytest.mark.parametrize("variant", ["inv1043", "INV 1043", "inv-1043", "Inv.1043"])
def test_formatting_variants_of_the_number_are_hard_duplicates(variant: str) -> None:
    assert check(ref("new", number=variant)).match.kind is DuplicateKind.HARD  # type: ignore[union-attr]


def test_a_hard_duplicate_needs_no_matching_total_or_date() -> None:
    other = ref("new", total=99999, day=date(2026, 12, 1))
    r = check(other)
    assert r.outcome is Outcome.FAIL and r.match.kind is DuplicateKind.HARD  # type: ignore[union-attr]


def test_a_hard_duplicate_works_even_when_the_total_or_date_is_missing() -> None:
    r = check(ref("new", total=None, day=None, currency=None))
    assert r.outcome is Outcome.FAIL


def test_another_suppliers_invoice_with_the_same_number_is_not_a_duplicate() -> None:
    assert check(earlier=[ref("old", supplier="sup-2")]).outcome is Outcome.PASS


def test_an_invoice_is_never_a_duplicate_of_itself() -> None:
    assert check(earlier=[ref("new")]).outcome is Outcome.PASS


def test_the_first_hard_match_is_reported() -> None:
    r = check(earlier=[ref("a"), ref("b")])
    assert r.match.existing.id == "a"  # type: ignore[union-attr]


def test_hard_beats_soft_when_both_exist() -> None:
    soft = ref("soft", number="INV-1044")
    hard = ref("hard")
    r = check(earlier=[soft, hard])
    assert r.match.kind is DuplicateKind.HARD and r.match.existing.id == "hard"  # type: ignore[union-attr]


# ---- soft duplicates -----------------------------------------------------------------------


def soft(**over: object) -> InvoiceRef:
    return replace(ref("new", number="INV-1044"), **over)  # type: ignore[arg-type]


def test_a_near_number_with_the_same_total_and_date_is_a_soft_duplicate() -> None:
    r = check(soft())
    assert r.outcome is Outcome.FAIL and r.match is not None
    assert (
        r.match.kind is DuplicateKind.SOFT and r.match.similarity >= 85 and r.match.days_apart == 0
    )


def test_the_date_window_is_seven_days_inclusive() -> None:
    assert check(soft(invoice_date=date(2026, 5, 8))).outcome is Outcome.FAIL
    assert check(soft(invoice_date=date(2026, 4, 24))).outcome is Outcome.FAIL
    assert check(soft(invoice_date=date(2026, 5, 9))).outcome is Outcome.PASS


def test_the_window_is_configurable() -> None:
    wide = DedupeSettings(window_days=30)
    assert check(soft(invoice_date=date(2026, 5, 25)), settings=wide).outcome is Outcome.FAIL


def test_a_different_total_is_not_a_soft_duplicate() -> None:
    assert check(soft(total_minor=50001)).outcome is Outcome.PASS


def test_a_different_currency_is_not_a_soft_duplicate() -> None:
    assert check(soft(currency="EUR")).outcome is Outcome.PASS


def test_a_credit_note_is_not_a_duplicate_of_its_invoice() -> None:
    assert check(soft(total_minor=-50000)).outcome is Outcome.PASS


def test_two_credit_notes_for_the_same_amount_can_be_duplicates() -> None:
    both = check(soft(total_minor=-50000), earlier=[ref("old", total=-50000)])
    assert both.outcome is Outcome.FAIL


def test_a_dissimilar_number_is_not_a_soft_duplicate() -> None:
    assert check(soft(invoice_number="INV-9999")).outcome is Outcome.PASS


def test_the_similarity_threshold_is_configurable() -> None:
    strict = DedupeSettings(number_similarity_min=99)
    assert check(soft(), settings=strict).outcome is Outcome.PASS


def test_the_best_soft_match_wins() -> None:
    near = ref("near", number="INV-1045")
    r = check(soft(), earlier=[ref("far", number="INV-1099"), near])
    assert r.match.existing.id == "near"  # type: ignore[union-attr]


def test_soft_matches_prefer_the_higher_similarity() -> None:
    loose = DedupeSettings(number_similarity_min=75)
    new = ref("new", number="MIP-2026-0899")
    two_off = ref("a", number="MIP-2026-0779")
    one_off = ref("b", number="MIP-2026-0889")
    r = check(new, earlier=[two_off, one_off], settings=loose)
    assert r.match.existing.id == "b"  # type: ignore[union-attr]


def test_a_prefixed_number_and_a_bare_one_are_soft_duplicates() -> None:
    r = check(ref("new", number="1043"), earlier=[ref("old", number="INV-1043")])
    assert r.outcome is Outcome.FAIL and r.match.kind is DuplicateKind.SOFT  # type: ignore[union-attr]


# ---- passing, skipping ----------------------------------------------------------------------


def test_no_earlier_invoices_passes() -> None:
    r = check(earlier=[])
    assert r.outcome is Outcome.PASS and r.details["compared"] == 0


def test_a_plain_non_match_passes_and_says_how_many_were_compared() -> None:
    r = check(ref("new", number="INV-2000", total=1), earlier=[ORIGINAL, ref("o2", number="X-1")])
    assert r.outcome is Outcome.PASS and r.details["compared"] == 2


def test_no_supplier_means_the_check_cannot_be_done() -> None:
    r = check(ref("new", supplier=None))
    assert r.outcome is Outcome.SKIPPED and r.details["reason"] == "NO_SUPPLIER"


def test_no_invoice_number_means_the_check_cannot_be_done() -> None:
    for number in (None, "", "---"):
        r = check(ref("new", number=number))
        assert r.outcome is Outcome.SKIPPED and r.details["reason"] == "NO_INVOICE_NUMBER"


def test_missing_total_or_date_leaves_the_soft_rule_unevaluable() -> None:
    for gap in ({"total_minor": None}, {"invoice_date": None}, {"currency": None}):
        r = check(soft(**gap))
        assert r.outcome is Outcome.SKIPPED and r.details["reason"] == "INCOMPLETE_FOR_SOFT_MATCH"


def test_an_earlier_invoice_with_gaps_only_matters_when_its_number_is_close() -> None:
    gappy = ref("gappy", number="TOTALLY-DIFFERENT", total=None, day=None)
    assert check(soft(), earlier=[gappy]).outcome is Outcome.PASS
    close = ref("close", number="INV-1045", total=None, day=None)
    assert check(soft(), earlier=[close]).outcome is Outcome.SKIPPED


def test_a_soft_match_elsewhere_wins_over_an_unevaluable_one() -> None:
    close_gappy = ref("gappy", number="INV-1045", total=None)
    r = check(soft(), earlier=[close_gappy, ref("hit", number="INV-1045")])
    assert r.outcome is Outcome.FAIL and r.match.existing.id == "hit"  # type: ignore[union-attr]


# ---- details -------------------------------------------------------------------------------


def test_details_name_the_existing_invoice_for_a_side_by_side_view() -> None:
    r = check(soft(invoice_date=date(2026, 5, 3)))
    assert r.details == {
        "kind": "soft",
        "existing_invoice_id": "old",
        "existing_invoice_number": "INV-1043",
        "existing_invoice_date": "2026-05-01",
        "similarity": r.details["similarity"],
        "days_apart": 2,
        "compared": 1,
    }


def test_untrusted_numbers_are_clipped_in_the_details() -> None:
    r = check(ref("new", number="A" * 500), earlier=[ref("old", number="A" * 500)])
    assert len(str(r.details["existing_invoice_number"])) <= 100


def test_results_carry_a_rule_version_and_serialize() -> None:
    import json

    r = check()
    assert r.version == RULE_VERSION
    json.dumps(r.details)


# ---- settings ------------------------------------------------------------------------------


def test_defaults_match_the_agreed_values() -> None:
    s = DedupeSettings()
    assert (s.window_days, s.number_similarity_min) == (7, 85)


def test_settings_come_from_the_tenant_with_safe_fallbacks() -> None:
    assert DedupeSettings.from_tenant({"dedupe_window_days": 14}).window_days == 14
    bad = {"dedupe_window_days": -1, "dedupe_number_similarity_min": "high"}
    assert DedupeSettings.from_tenant(bad) == DedupeSettings()
    assert DedupeSettings.from_tenant({"dedupe_number_similarity_min": 101}) == DedupeSettings()


def test_passed_is_true_only_for_a_pass() -> None:
    assert check(earlier=[]).passed is True
    assert check().passed is False
    assert check(ref("new", supplier=None)).passed is False


def test_an_earlier_invoice_without_a_number_is_ignored() -> None:
    nameless = ref("old", number=None)
    assert check(soft(), earlier=[nameless]).outcome is Outcome.PASS
