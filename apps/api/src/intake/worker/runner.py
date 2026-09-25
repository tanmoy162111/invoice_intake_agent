"""Worker loop: reap stale jobs, claim one, run its handler, record the outcome."""

import logging
import signal
import threading
import uuid

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.config import Settings
from intake.core.llm_budget import ExtractionFailure
from intake.core.statuses import ActorType
from intake.db.invoices import fail_invoice_after_job_gave_up
from intake.db.models import Job
from intake.extract.pipeline import EXTRACT_JOB
from intake.ingest.service import PROCESS_JOB
from intake.ingest.storage import LocalStorage
from intake.worker import queue
from intake.worker.handlers import HANDLERS, Handler

log = logging.getLogger("intake.worker")


def run_once(
    engine: Engine, storage: LocalStorage, settings: Settings,
    handlers: dict[str, Handler] | None = None,
) -> bool:  # fmt: skip
    """Process at most one job. Returns True if a job was claimed (whether or not it succeeded)."""
    handlers = handlers if handlers is not None else HANDLERS
    with Session(engine) as session:
        reaped = queue.reap_stale(
            session,
            timeout_s=settings.job_visibility_timeout_s,
            max_attempts=settings.job_max_attempts,
        )
        if reaped:
            log.warning("re-queued or failed %d stale job(s)", reaped)
        job = queue.claim(session)
        session.commit()  # publish "running" and release the row lock
        if job is None:
            return False
        job_id, job_type, attempt = job.id, job.type, job.attempts

    with Session(engine) as session:
        job = session.get_one(Job, job_id)
        try:
            outcome = handlers[job_type](session, storage, settings, job)
            if isinstance(outcome, queue.Deferral):
                if queue.defer(
                    session, job, until=outcome.until, reason=outcome.reason, attempt=attempt
                ):
                    session.commit()
                    # Routine waiting is not a warning; a spend cap or missing setup is.
                    level = (
                        logging.INFO if outcome.reason.startswith("WAITING") else logging.WARNING
                    )
                    log.log(level, "job %s (%s) paused: %s", job_id, job_type, outcome.reason)
                else:
                    session.rollback()
                    log.warning("job %s paused after losing its lock; ignored", job_id)
            elif queue.complete(session, job, attempt=attempt):
                session.commit()
                log.info("job %s (%s) done", job_id, job_type)
            else:
                session.rollback()  # the job was re-queued or re-claimed meanwhile
                log.warning("job %s finished after losing its lock; result discarded", job_id)
        except Exception as exc:
            session.rollback()
            # Only the exception class is stored and logged: messages could echo document content.
            log.debug("job %s error detail", job_id, exc_info=True)
            _record_failure(session, job_id, exc.__class__.__name__, attempt, settings)
    return True


def _record_failure(
    session: Session, job_id: uuid.UUID, error: str, attempt: int, settings: Settings
) -> None:
    job = session.get_one(Job, job_id)
    retrying = queue.fail(
        session, job, error,
        max_attempts=settings.job_max_attempts,
        base_s=settings.job_backoff_base_s, cap_s=settings.job_backoff_cap_s,
        attempt=attempt,
    )  # fmt: skip
    if retrying is None:
        session.rollback()
        log.warning("job %s failed after losing its lock; ignored", job_id)
        return
    invoice_id = job.payload.get("invoice_id")
    if not retrying and invoice_id and job.type in (PROCESS_JOB, EXTRACT_JOB):
        fail_invoice_after_job_gave_up(
            session, uuid.UUID(invoice_id), f"{ExtractionFailure.JOB_FAILED}:{error}"
        )
    record_event(
        session, tenant_id=job.tenant_id,
        invoice_id=uuid.UUID(invoice_id) if invoice_id else None,
        event_type="job_retry_scheduled" if retrying else "job_failed",
        actor_type=ActorType.SYSTEM, actor_id="worker",
        data={"job_id": str(job.id), "type": job.type, "attempt": job.attempts, "error": error},
    )  # fmt: skip
    session.commit()
    log.warning("job %s failed (%s): %s", job_id, "will retry" if retrying else "gave up", error)


def run_forever(engine: Engine, storage: LocalStorage, settings: Settings) -> None:
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info("worker started")
    while not stop.is_set():
        try:
            if run_once(engine, storage, settings):
                continue  # more work may be waiting
        except Exception:
            log.exception("worker loop error")
        stop.wait(settings.worker_poll_interval_s)
    log.info("worker stopped")
