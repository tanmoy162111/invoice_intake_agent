"""Pure retry policy for the job queue."""


def backoff_seconds(attempts: int, *, base: int, cap: int) -> int:
    """Delay before the next attempt: base, 2*base, 4*base ... capped. `attempts` is the number
    of attempts already made (>= 1)."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    return min(cap, base * (1 << (attempts - 1)))


def should_retry(*, attempts: int, max_attempts: int) -> bool:
    return attempts < max_attempts
