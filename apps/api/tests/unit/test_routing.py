import pytest

from intake.core.exceptions import ExceptionCode, Severity
from intake.core.routing import (
    RoutedException,
    RoutingInput,
    RoutingReason,
    decide_route,
)
from intake.core.statuses import InvoiceStatus, Route


def routing(**kw: object) -> RoutingInput:
    base: dict[str, object] = {
        "exceptions": (),
        "weak_fields": (),
        "missing_checks": (),
        "total_minor": 500_000,
        "approval_limit_minor": 1_000_000,
    }
    return RoutingInput(**{**base, **kw})  # type: ignore[arg-type]


def exc(code: ExceptionCode, severity: Severity) -> RoutedException:
    return RoutedException(code, severity)


def test_a_clean_invoice_is_cleared_straight_through() -> None:
    d = decide_route(routing())
    assert (d.status, d.route, d.reasons) == (InvoiceStatus.CLEARED, Route.STRAIGHT_THROUGH, ())


@pytest.mark.parametrize("severity", [Severity.REVIEW, Severity.BLOCK])
def test_an_open_review_or_block_exception_needs_review(severity: Severity) -> None:
    d = decide_route(routing(exceptions=(exc(ExceptionCode.PRICE_VARIANCE, severity),)))
    assert (d.status, d.route) == (InvoiceStatus.NEEDS_REVIEW, Route.REVIEW)
    assert d.reasons == (RoutingReason.OPEN_EXCEPTIONS,)


def test_an_info_exception_does_not_stop_an_invoice() -> None:
    d = decide_route(routing(exceptions=(exc(ExceptionCode.NO_PO, Severity.INFO),)))
    assert d.status is InvoiceStatus.CLEARED


@pytest.mark.parametrize("severity", list(Severity))
def test_a_bank_account_change_always_needs_review_whatever_its_severity(
    severity: Severity,
) -> None:
    d = decide_route(routing(exceptions=(exc(ExceptionCode.BANK_DETAILS_CHANGED, severity),)))
    assert d.status is InvoiceStatus.NEEDS_REVIEW
    assert RoutingReason.BANK_DETAILS_CHANGED in d.reasons


def test_a_low_confidence_critical_field_needs_review_even_with_no_exception() -> None:
    d = decide_route(routing(weak_fields=("total",)))
    assert d.status is InvoiceStatus.NEEDS_REVIEW
    assert d.reasons == (RoutingReason.LOW_CONFIDENCE,)


def test_a_total_at_the_limit_clears_and_one_unit_above_needs_review() -> None:
    assert decide_route(routing(total_minor=1_000_000)).status is InvoiceStatus.CLEARED
    d = decide_route(routing(total_minor=1_000_001))
    assert d.status is InvoiceStatus.NEEDS_REVIEW and d.reasons == (RoutingReason.ABOVE_LIMIT,)


def test_a_missing_total_or_limit_is_never_cleared() -> None:
    assert decide_route(routing(total_minor=None)).reasons == (RoutingReason.NO_TOTAL,)
    assert decide_route(routing(approval_limit_minor=None)).reasons == (RoutingReason.NO_LIMIT,)


def test_a_check_that_never_ran_needs_review() -> None:
    d = decide_route(routing(missing_checks=("PRICE_VARIANCE",)))
    assert d.status is InvoiceStatus.NEEDS_REVIEW and d.reasons == (RoutingReason.MISSING_CHECKS,)


def test_every_reason_is_reported_in_a_fixed_order() -> None:
    d = decide_route(
        routing(
            exceptions=(
                exc(ExceptionCode.BANK_DETAILS_CHANGED, Severity.BLOCK),
                exc(ExceptionCode.PRICE_VARIANCE, Severity.REVIEW),
            ),
            weak_fields=("currency",),
            missing_checks=("NO_PO",),
            total_minor=2_000_000,
        )
    )
    assert d.reasons == (
        RoutingReason.BANK_DETAILS_CHANGED,
        RoutingReason.OPEN_EXCEPTIONS,
        RoutingReason.LOW_CONFIDENCE,
        RoutingReason.ABOVE_LIMIT,
        RoutingReason.MISSING_CHECKS,
    )


def test_a_negative_total_is_never_cleared() -> None:
    d = decide_route(routing(total_minor=-100))
    assert d.status is InvoiceStatus.NEEDS_REVIEW and d.reasons == (RoutingReason.CREDIT_NOTE,)
