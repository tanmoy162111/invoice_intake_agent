"""Postgres-backed job queue: SELECT ... FOR UPDATE SKIP LOCKED, retries with backoff, and a
reaper for jobs whose worker died. All timestamps come from the database clock."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.core.retry import backoff_seconds, should_retry
from intake.core.statuses import ActorType, JobStatus
from intake.db.models import Job

MAX_ERROR_CHARS = 500
# Reasons a job is paused on purpose (not failed). Deferring does not use up an attempt.
PAUSE_REASONS = ("SPEND_CAP_REACHED", "EXTRACTION_NOT_CONFIGURED")


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


def _still_ours(session: Session, job: Job, attempt: int | None) -> bool:
    """Lock the row and check this worker still owns the run. If the reaper re-queued the job
    (or another worker re-claimed it) while we were running, our result must not be recorded."""
    session.refresh(job, with_for_update=True)
    if job.status != JobStatus.RUNNING.value:
        return False
    return attempt is None or job.attempts == attempt


def complete(session: Session, job: Job, *, attempt: int | None = None) -> bool:
    if not _still_ours(session, job, attempt):
        return False
    job.status = JobStatus.DONE.value
    job.locked_at = None
    job.last_error = None
    session.flush()
    return True


def defer(
    session: Session, job: Job, *, until: datetime, reason: str, attempt: int | None = None
) -> bool:
    """Pause a running job until `until` without counting the run as an attempt (the work never
    started). Returns False, recording nothing, if this worker no longer owns the job."""
    if not _still_ours(session, job, attempt):
        return False
    job.status = JobStatus.QUEUED.value
    job.attempts = max(job.attempts - 1, 0)
    job.run_after = until
    job.locked_at = None
    job.last_error = reason
    session.flush()
    return True


def fail(
    session: Session,
    job: Job,
    error: str,
    *,
    max_attempts: int,
    base_s: int,
    cap_s: int,
    attempt: int | None = None,
) -> bool | None:
    """Record a failure. Returns True if the job will be retried, False if it is now failed, None
    if this worker no longer owns the job (nothing recorded)."""
    if not _still_ours(session, job, attempt):
        return None
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
    """Handle running jobs whose lock expired (worker crashed): re-queue them, or fail them if they
    used all attempts. Uses the same retry rule as `fail` and audits every job it touches."""
    stale = session.execute(
        select(Job)
        .where(
            Job.status == JobStatus.RUNNING.value,
            Job.locked_at < func.now() - timedelta(seconds=timeout_s),
        )
        .with_for_update(skip_locked=True)
    ).scalars()
    touched = 0
    for job in stale:
        retry = should_retry(attempts=job.attempts, max_attempts=max_attempts)
        job.locked_at = None
        if retry:
            job.status = JobStatus.QUEUED.value
            job.run_after = func.now()
        else:
            job.status = JobStatus.FAILED.value
            job.last_error = "WorkerLost"
        invoice_id = job.payload.get("invoice_id")
        record_event(
            session,
            tenant_id=job.tenant_id,
            invoice_id=uuid.UUID(invoice_id) if invoice_id else None,
            event_type="job_requeued_after_lost_worker" if retry else "job_failed",
            actor_type=ActorType.SYSTEM,
            actor_id="reaper",
            data={"job_id": str(job.id), "type": job.type, "attempt": job.attempts},
        )
        touched += 1
    session.flush()
    return touched


@dataclass(frozen=True)
class QueueStats:
    counts: dict[str, int]
    stuck: int
    oldest_queued_age_s: float | None
    paused: int = 0  # deferred on purpose (spend cap, extraction not configured)


def stats(session: Session, *, timeout_s: int, tenant_id: uuid.UUID | None = None) -> QueueStats:
    """Queue health. Pass tenant_id to scope it to one tenant (the API always does)."""
    scope = [Job.tenant_id == tenant_id] if tenant_id else []
    rows = session.execute(
        select(Job.status, func.count()).where(*scope).group_by(Job.status)
    ).all()
    counts = {s.value: 0 for s in JobStatus}
    counts.update({status: n for status, n in rows})
    stuck = session.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            *scope,
            Job.status == JobStatus.RUNNING.value,
            Job.locked_at < func.now() - timedelta(seconds=timeout_s),
        )
    )
    age = session.scalar(
        select(func.extract("epoch", func.now() - func.min(Job.run_after))).where(
            *scope, Job.status == JobStatus.QUEUED.value, Job.run_after <= func.now()
        )
    )
    paused = session.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            *scope,
            Job.status == JobStatus.QUEUED.value,
            Job.run_after > func.now(),
            Job.last_error.in_(PAUSE_REASONS),
        )
    )
    return QueueStats(
        counts, int(stuck or 0), float(age) if age is not None else None, int(paused or 0)
    )
