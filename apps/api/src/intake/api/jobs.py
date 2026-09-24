import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from intake.api.deps import session_dep, settings_dep
from intake.config import Settings
from intake.core.statuses import JobStatus
from intake.db.models import Job
from intake.worker import queue

router = APIRouter(prefix="/jobs", tags=["jobs"])


class FailedJob(BaseModel):
    id: uuid.UUID
    type: str
    attempts: int
    last_error: str | None


class JobsOut(BaseModel):
    counts: dict[str, int]
    stuck: int
    paused: int  # deferred on purpose: spend cap reached or extraction not configured
    oldest_queued_age_s: float | None
    failed: list[FailedJob]


@router.get("", response_model=JobsOut)
def job_overview(
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> JobsOut:
    """Queue health: counts by status, jobs stuck past the visibility timeout, recent failures."""
    tenant_id = uuid.UUID(settings.default_tenant_id)
    st = queue.stats(session, timeout_s=settings.job_visibility_timeout_s, tenant_id=tenant_id)
    failed = session.execute(
        select(Job)
        .where(Job.tenant_id == tenant_id, Job.status == JobStatus.FAILED.value)
        .order_by(Job.created_at.desc())
        .limit(20)
    ).scalars()
    return JobsOut(
        counts=st.counts,
        stuck=st.stuck,
        paused=st.paused,
        oldest_queued_age_s=st.oldest_queued_age_s,
        failed=[
            FailedJob(id=j.id, type=j.type, attempts=j.attempts, last_error=j.last_error)
            for j in failed
        ],
    )
