"""Straight-through or review (playbook §6.7). Pure: what is known about an invoice in, a decision.

An invoice is `cleared` only when nothing at all is in doubt. Uncertain means human: an exception,
a doubtful critical field, a total above the limit, a missing, zero or negative total, a missing
limit, or a check that did not run each send it to review. A bank-account change always does,
whatever its severity; a bank check that merely could not be done is an exception, not a change.
`RoutingReason` says why, so the reviewer and the audit trail can show it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from intake.core.exceptions import ExceptionCode, Severity
from intake.core.statuses import InvoiceStatus, Route


class RoutingReason(StrEnum):
    BANK_DETAILS_CHANGED = "BANK_DETAILS_CHANGED"
    OPEN_EXCEPTIONS = "OPEN_EXCEPTIONS"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NO_TOTAL = "NO_TOTAL"
    CREDIT_NOTE = "CREDIT_NOTE"
    ZERO_TOTAL = "ZERO_TOTAL"
    NO_LIMIT = "NO_LIMIT"
    ABOVE_LIMIT = "ABOVE_LIMIT"
    MISSING_CHECKS = "MISSING_CHECKS"


@dataclass(frozen=True)
class RoutedException:
    code: ExceptionCode
    severity: Severity
    unchecked: bool = False  # the check could not be done; it did not find the problem


@dataclass(frozen=True)
class RoutingInput:
    exceptions: Sequence[RoutedException]  # the open exceptions of the invoice
    weak_fields: Sequence[str]  # critical fields missing or below the confidence minimum
    missing_checks: Sequence[str]  # checks that should have run and have no result
    total_minor: int | None
    approval_limit_minor: int | None  # in the invoice's own currency; None means none configured


@dataclass(frozen=True)
class RoutingDecision:
    status: InvoiceStatus
    route: Route
    reasons: tuple[RoutingReason, ...]


def _limit(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def approval_limit_for(settings: Mapping[str, object], currency: str | None) -> int | None:
    """The approval limit that applies to an invoice in `currency`, in minor units, or None.

    A limit is only ever compared with a total in the same currency (there are no exchange rates):
    `approval_amount_limits_minor` maps a currency to its limit, and `approval_amount_limit_minor`
    is the limit for `approval_limit_currency` (default USD). No limit for the currency means the
    invoice goes to review."""
    if not currency:
        return None
    per_currency = settings.get("approval_amount_limits_minor")
    if isinstance(per_currency, Mapping) and _limit(per_currency.get(currency)) is not None:
        return _limit(per_currency.get(currency))
    if currency == settings.get("approval_limit_currency", "USD"):
        return _limit(settings.get("approval_amount_limit_minor"))
    return None


def decide_route(x: RoutingInput) -> RoutingDecision:
    reasons: list[RoutingReason] = []
    if any(e.code is ExceptionCode.BANK_DETAILS_CHANGED and not e.unchecked for e in x.exceptions):
        reasons.append(RoutingReason.BANK_DETAILS_CHANGED)
    if any(e.severity in (Severity.REVIEW, Severity.BLOCK) for e in x.exceptions):
        reasons.append(RoutingReason.OPEN_EXCEPTIONS)
    if x.weak_fields:
        reasons.append(RoutingReason.LOW_CONFIDENCE)
    if x.total_minor is None:
        reasons.append(RoutingReason.NO_TOTAL)
    elif x.total_minor < 0:
        reasons.append(RoutingReason.CREDIT_NOTE)
    elif x.total_minor == 0:  # a zero total is far more likely a misread than a real invoice
        reasons.append(RoutingReason.ZERO_TOTAL)
    if x.approval_limit_minor is None:
        reasons.append(RoutingReason.NO_LIMIT)
    elif x.total_minor is not None and x.total_minor > x.approval_limit_minor:
        reasons.append(RoutingReason.ABOVE_LIMIT)
    if x.missing_checks:
        reasons.append(RoutingReason.MISSING_CHECKS)
    if reasons:
        return RoutingDecision(InvoiceStatus.NEEDS_REVIEW, Route.REVIEW, tuple(reasons))
    return RoutingDecision(InvoiceStatus.CLEARED, Route.STRAIGHT_THROUGH, ())
