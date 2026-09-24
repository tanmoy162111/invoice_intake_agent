import pytest

from intake.core.retry import backoff_seconds, should_retry
from intake.core.statuses import InvoiceStatus as S
from intake.core.workflow import can_transition


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (S.RECEIVED, S.EXTRACTING),
        (S.EXTRACTING, S.EXTRACTED),
        (S.EXTRACTING, S.FAILED),
        (S.FAILED, S.EXTRACTING),
        (S.EXTRACTED, S.CHECKING),
        (S.CHECKING, S.CLEARED),
        (S.CHECKING, S.NEEDS_REVIEW),
        (S.CLEARED, S.APPROVED),
        (S.NEEDS_REVIEW, S.APPROVED),
        (S.NEEDS_REVIEW, S.REJECTED),
        (S.APPROVED, S.EXPORTED),
    ],
)
def test_allowed_transitions(a: S, b: S) -> None:
    assert can_transition(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (S.RECEIVED, S.APPROVED),  # cannot skip the checks
        (S.RECEIVED, S.CLEARED),
        (S.EXTRACTED, S.APPROVED),
        (S.REJECTED, S.APPROVED),
        (S.EXPORTED, S.RECEIVED),
        (S.APPROVED, S.NEEDS_REVIEW),
        (S.RECEIVED, S.RECEIVED),
    ],
)
def test_forbidden_transitions(a: S, b: S) -> None:
    assert not can_transition(a, b)


def test_every_status_is_known_to_the_table() -> None:
    from intake.core.workflow import TRANSITIONS

    assert set(TRANSITIONS) == set(S)


def test_backoff_doubles_and_caps() -> None:
    assert [backoff_seconds(n, base=10, cap=100) for n in (1, 2, 3, 4, 5, 6)] == [
        10, 20, 40, 80, 100, 100,
    ]  # fmt: skip


def test_backoff_rejects_non_positive_attempts() -> None:
    with pytest.raises(ValueError):
        backoff_seconds(0, base=10, cap=100)


def test_should_retry() -> None:
    assert should_retry(attempts=1, max_attempts=3)
    assert should_retry(attempts=2, max_attempts=3)
    assert not should_retry(attempts=3, max_attempts=3)


def test_transition_table_is_exactly_the_playbook_diagram() -> None:
    from intake.core.workflow import TRANSITIONS

    edges = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}
    assert edges == {
        (S.RECEIVED, S.EXTRACTING),
        (S.EXTRACTING, S.EXTRACTED),
        (S.EXTRACTING, S.FAILED),
        (S.FAILED, S.EXTRACTING),
        (S.EXTRACTED, S.CHECKING),
        (S.CHECKING, S.CLEARED),
        (S.CHECKING, S.NEEDS_REVIEW),
        (S.CLEARED, S.APPROVED),
        (S.NEEDS_REVIEW, S.APPROVED),
        (S.NEEDS_REVIEW, S.REJECTED),
        (S.APPROVED, S.EXPORTED),
    }


def test_backoff_survives_huge_attempt_counts() -> None:
    assert backoff_seconds(10_000, base=10, cap=600) == 600
    assert backoff_seconds(3, base=100, cap=50) == 50  # cap below base still caps
