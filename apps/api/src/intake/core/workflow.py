"""Allowed invoice status transitions (playbook §5.2). Every change also writes an audit event."""

from intake.core.statuses import InvoiceStatus as S

TRANSITIONS: dict[S, frozenset[S]] = {
    S.RECEIVED: frozenset({S.EXTRACTING}),
    S.EXTRACTING: frozenset({S.EXTRACTED, S.FAILED}),
    S.FAILED: frozenset({S.EXTRACTING}),
    S.EXTRACTED: frozenset({S.CHECKING}),
    S.CHECKING: frozenset({S.CLEARED, S.NEEDS_REVIEW}),
    S.CLEARED: frozenset({S.APPROVED, S.NEEDS_REVIEW}),
    S.NEEDS_REVIEW: frozenset({S.APPROVED, S.REJECTED}),
    S.APPROVED: frozenset({S.EXPORTED}),
    S.REJECTED: frozenset(),
    S.EXPORTED: frozenset(),
}


def can_transition(current: S, new: S) -> bool:
    return new in TRANSITIONS[current]
