"""Straight-through or review (playbook §6.7). Pure: what is known about an invoice in, a decision.

An invoice is `cleared` only when nothing at all is in doubt. Uncertain means human: an exception,
a doubtful critical field, a total above the limit, a missing total or limit, a credit note, or a
check that did not run each send it to review. A bank-account change always does, whatever its
severity. `RoutingReason` says why, so the reviewer and the audit trail can show it.
"""

from collections.abc import Sequence
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
    NO_LIMIT = "NO_LIMIT"
    ABOVE_LIMIT = "ABOVE_LIMIT"
    MISSING_CHECKS = "MISSING_CHECKS"


@dataclass(frozen=True)
class RoutedException:
    code: ExceptionCode
    severity: Severity


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


def decide_route(x: RoutingInput) -> RoutingDecision:
    reasons: list[RoutingReason] = []
    if any(e.code is ExceptionCode.BANK_DETAILS_CHANGED for e in x.exceptions):
        reasons.append(RoutingReason.BANK_DETAILS_CHANGED)
    if any(e.severity in (Severity.REVIEW, Severity.BLOCK) for e in x.exceptions):
        reasons.append(RoutingReason.OPEN_EXCEPTIONS)
    if x.weak_fields:
        reasons.append(RoutingReason.LOW_CONFIDENCE)
    if x.total_minor is None:
        reasons.append(RoutingReason.NO_TOTAL)
    elif x.total_minor < 0:
        reasons.append(RoutingReason.CREDIT_NOTE)
    if x.approval_limit_minor is None:
        reasons.append(RoutingReason.NO_LIMIT)
    elif x.total_minor is not None and x.total_minor > x.approval_limit_minor:
        reasons.append(RoutingReason.ABOVE_LIMIT)
    if x.missing_checks:
        reasons.append(RoutingReason.MISSING_CHECKS)
    if reasons:
        return RoutingDecision(InvoiceStatus.NEEDS_REVIEW, Route.REVIEW, tuple(reasons))
    return RoutingDecision(InvoiceStatus.CLEARED, Route.STRAIGHT_THROUGH, ())
