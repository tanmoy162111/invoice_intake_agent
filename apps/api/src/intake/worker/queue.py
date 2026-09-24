"""Postgres-backed job queue: SELECT ... FOR UPDATE SKIP LOCKED, retries with backoff, and a
reaper for jobs whose worker died. All timestamps come from the database clock."""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from intake.core.retry import backoff_seconds, should_retry
from intake.core.statuses import JobStatus
from intake.db.models import Job

MAX_ERROR_CHARS = 500


def enqueue(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    type: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
) -> Job:
    """Add a job. With a dedupe_key, enqueueing twice returns the existing job."""
    stmt = (
        insert(Job)
        .values(tenant_id=tenant_id, type=type, payload=payload, dedupe_key=dedupe_key)
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(Job.id)
    )
    job_id = session.execute(stmt).scalar()
    if job_id is None:  # conflict: the job already exists
        return session.execute(select(Job).where(Job.dedupe_key == dedupe_key)).scalar_one()
    return session.get_one(Job, job_id)


def claim(session: Session) -> Job | None:
    """Take the oldest runnable job, marking it running. Concurrent workers never get the same job.
    The caller commits to release the row lock and publish the new state."""
    job = session.execute(
        select(Job)
        .where(Job.status == JobStatus.QUEUED.value, Job.run_after <= func.now())
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if job is None:
        return None
    job.status = JobStatus.RUNNING.value
    job.locked_at = func.now()
    job.attempts += 1
    session.flush()
    session.refresh(job)
    return job


def complete(session: Session, job: Job) -> None:
    job.status = JobStatus.DONE.value
    job.locked_at = None
    job.last_error = None
    session.flush()


def fail(
    session: Session, job: Job, error: str, *, max_attempts: int, base_s: int, cap_s: int
) -> bool:
    """Record a failure. Returns True if the job will be retried, False if it is now failed."""
    job.last_error = error[:MAX_ERROR_CHARS]
    job.locked_at = None
    if should_retry(attempts=job.attempts, max_attempts=max_attempts):
        delay = backoff_seconds(job.attempts, base=base_s, cap=cap_s)
        job.status = JobStatus.QUEUED.value
        job.run_after = func.now() + timedelta(seconds=delay)
        session.flush()
        return True
    job.status = JobStatus.FAILED.value
    session.flush()
    return False


def reap_stale(session: Session, *, timeout_s: int, max_attempts: int) -> int:
    """Re-queue running jobs whose lock expired (worker crashed). Jobs that already used all
    their attempts are marked failed instead. Returns how many jobs were touched."""
    stale = (
        Job.status == JobStatus.RUNNING.value,
        Job.locked_at < func.now() - timedelta(seconds=timeout_s),
    )
    failed = cast(
        "CursorResult[Any]",
        session.execute(
            update(Job)
            .where(*stale, Job.attempts >= max_attempts)
            .values(
                status=JobStatus.FAILED.value,
                locked_at=None,
                last_error="worker lost the job and no attempts remain",
            )
        ),
    ).rowcount
    requeued = cast(
        "CursorResult[Any]",
        session.execute(
            update(Job)
            .where(*stale, Job.attempts < max_attempts)
            .values(status=JobStatus.QUEUED.value, locked_at=None, run_after=func.now())
        ),
    ).rowcount
    return int(failed + requeued)


@dataclass(frozen=True)
class QueueStats:
    counts: dict[str, int]
    stuck: int
    oldest_queued_age_s: float | None


def stats(session: Session, *, timeout_s: int) -> QueueStats:
    rows = session.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    counts = {s.value: 0 for s in JobStatus}
    counts.update({status: n for status, n in rows})
    stuck = session.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.status == JobStatus.RUNNING.value,
            Job.locked_at < func.now() - timedelta(seconds=timeout_s),
        )
    )
    age = session.scalar(
        text(
            "select extract(epoch from now() - min(run_after)) from jobs "
            "where status = 'queued' and run_after <= now()"
        )
    )
    return QueueStats(counts, int(stuck or 0), float(age) if age is not None else None)
