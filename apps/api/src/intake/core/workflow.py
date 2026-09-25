"""Allowed invoice status transitions (playbook §5.2). Every change also writes an audit event."""

from intake.core.statuses import InvoiceStatus as S

TRANSITIONS: dict[S, frozenset[S]] = {
    S.RECEIVED: frozenset({S.EXTRACTING}),
    S.EXTRACTING: frozenset({S.EXTRACTED, S.FAILED}),
    S.FAILED: frozenset({S.EXTRACTING}),
    S.EXTRACTED: frozenset({S.CHECKING}),
    S.CHECKING: frozenset({S.CLEARED, S.NEEDS_REVIEW}),
    # M8: correcting a field sends an open invoice back through the checks (extracted), and a
    # reviewer can reject a cleared invoice as well as one that needs review.
    S.CLEARED: frozenset({S.APPROVED, S.REJECTED, S.EXTRACTED}),
    S.NEEDS_REVIEW: frozenset({S.APPROVED, S.REJECTED, S.EXTRACTED}),
    S.APPROVED: frozenset({S.EXPORTED}),
    S.REJECTED: frozenset(),
    S.EXPORTED: frozenset(),
}


def can_transition(current: S, new: S) -> bool:
    return new in TRANSITIONS[current]
