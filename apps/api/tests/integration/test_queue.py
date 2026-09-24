import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Engine, func, text, update
from sqlalchemy.orm import Session

from intake.core.statuses import ActorType, InvoiceStatus, JobStatus
from intake.db.invoices import InvalidTransition, set_invoice_status
from intake.db.models import Document, Invoice, Job, Tenant
from intake.worker import queue

KW = {"max_attempts": 3, "base_s": 10, "cap_s": 600}


@pytest.fixture
def tenant_id(engine: Engine) -> uuid.UUID:
    with Session(engine) as s:
        t = Tenant(name="queue-test")
        s.add(t)
        s.commit()
        tid = t.id
    with engine.begin() as conn:
        conn.execute(text("delete from jobs"))  # only this suite uses jobs; keeps tests isolated
    return tid


def add_job(session: Session, tid: uuid.UUID, key: str | None = None) -> Job:
    job = queue.enqueue(session, tenant_id=tid, type="t", payload={"n": 1}, dedupe_key=key)
    session.commit()
    return job


def test_claim_marks_running_and_counts_attempt(session: Session, tenant_id: uuid.UUID) -> None:
    add_job(session, tenant_id)
    job = queue.claim(session)
    assert job is not None
    assert (job.status, job.attempts) == ("running", 1)
    assert job.locked_at is not None
    session.commit()
    assert queue.claim(session) is None  # nothing else runnable


def test_claim_returns_none_when_empty(session: Session, tenant_id: uuid.UUID) -> None:
    assert queue.claim(session) is None


def test_enqueue_with_dedupe_key_is_idempotent(session: Session, tenant_id: uuid.UUID) -> None:
    a = add_job(session, tenant_id, key="doc:1")
    b = add_job(session, tenant_id, key="doc:1")
    assert a.id == b.id
    assert session.scalar(text("select count(*) from jobs")) == 1


def test_complete(session: Session, tenant_id: uuid.UUID) -> None:
    add_job(session, tenant_id)
    job = queue.claim(session)
    assert job is not None
    queue.complete(session, job)
    assert (job.status, job.locked_at) == ("done", None)


def test_failure_retries_with_backoff_then_gives_up(session: Session, tenant_id: uuid.UUID) -> None:
    add_job(session, tenant_id)
    for attempt, delay in [(1, 10), (2, 20)]:
        job = queue.claim(session)
        assert job is not None and job.attempts == attempt
        assert queue.fail(session, job, "boom", **KW) is True
        session.commit()
        wait = session.scalar(text("select extract(epoch from run_after - now()) from jobs"))
        assert delay - 2 <= wait <= delay + 1
        assert queue.claim(session) is None  # not runnable until the backoff passes
        session.execute(update(Job).values(run_after=func.now()))  # fast-forward
        session.commit()
    job = queue.claim(session)
    assert job is not None and job.attempts == 3
    assert queue.fail(session, job, "boom again", **KW) is False
    assert (job.status, job.last_error) == ("failed", "boom again")


def test_error_text_is_truncated(session: Session, tenant_id: uuid.UUID) -> None:
    add_job(session, tenant_id)
    job = queue.claim(session)
    assert job is not None
    queue.fail(session, job, "x" * 5000, **KW)
    assert job.last_error is not None and len(job.last_error) == queue.MAX_ERROR_CHARS


def test_reaper_requeues_stale_running_jobs(session: Session, tenant_id: uuid.UUID) -> None:
    add_job(session, tenant_id)
    job = queue.claim(session)
    assert job is not None
    session.commit()
    assert queue.reap_stale(session, timeout_s=300, max_attempts=3) == 0  # lock is fresh
    session.execute(update(Job).values(locked_at=func.now() - timedelta(seconds=600)))
    session.commit()
    assert queue.stats(session, timeout_s=300).stuck == 1
    assert queue.reap_stale(session, timeout_s=300, max_attempts=3) == 1
    session.commit()
    session.refresh(job)
    assert (job.status, job.attempts) == ("queued", 1)
    again = queue.claim(session)
    assert again is not None and again.attempts == 2


def test_reaper_fails_jobs_out_of_attempts(session: Session, tenant_id: uuid.UUID) -> None:
    add_job(session, tenant_id)
    session.execute(
        update(Job).values(status="running", attempts=3, locked_at=func.now() - timedelta(hours=1))
    )
    session.commit()
    assert queue.reap_stale(session, timeout_s=300, max_attempts=3) == 1
    session.commit()
    assert session.scalar(text("select status from jobs")) == "failed"


def test_two_workers_never_claim_the_same_job(engine: Engine, tenant_id: uuid.UUID) -> None:
    with Session(engine) as s:
        for i in range(20):
            queue.enqueue(s, tenant_id=tenant_id, type="t", payload={}, dedupe_key=f"k{i}")
        s.commit()
    claimed: list[list[uuid.UUID]] = [[], []]

    def worker(n: int) -> None:
        with Session(engine) as s:
            while (job := queue.claim(s)) is not None:
                s.commit()
                claimed[n].append(job.id)

    threads = [threading.Thread(target=worker, args=(n,)) for n in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    all_ids = claimed[0] + claimed[1]
    assert len(all_ids) == 20 and len(set(all_ids)) == 20


def test_stats_counts_and_oldest_age(session: Session, tenant_id: uuid.UUID) -> None:
    add_job(session, tenant_id, key="a")
    add_job(session, tenant_id, key="b")
    st = queue.stats(session, timeout_s=300)
    assert st.counts[JobStatus.QUEUED.value] == 2 and st.stuck == 0
    assert st.oldest_queued_age_s is not None and st.oldest_queued_age_s >= 0


def test_set_invoice_status_checks_transition_and_audits(
    session: Session, tenant_id: uuid.UUID
) -> None:
    doc = Document(
        tenant_id=tenant_id, file_sha256=uuid.uuid4().hex * 2, filename="a.pdf",
        mime="application/pdf", storage_path="x", source="test",
    )  # fmt: skip
    session.add(doc)
    session.flush()
    inv = Invoice(tenant_id=tenant_id, document_id=doc.id)
    session.add(inv)
    session.flush()
    session.refresh(inv)
    set_invoice_status(session, inv, InvoiceStatus.EXTRACTING, actor_type=ActorType.SYSTEM)
    with pytest.raises(InvalidTransition):
        set_invoice_status(session, inv, InvoiceStatus.APPROVED, actor_type=ActorType.SYSTEM)
    events = session.execute(
        text("select data from audit_events where invoice_id=:i and event_type='status_changed'"),
        {"i": inv.id},
    ).scalars().all()  # fmt: skip
    assert events == [{"from": "received", "to": "extracting"}]
    session.rollback()


def test_reaper_writes_audit_events(session: Session, tenant_id: uuid.UUID) -> None:
    add_job(session, tenant_id)
    session.execute(
        update(Job).values(status="running", attempts=1, locked_at=func.now() - timedelta(hours=1))
    )
    session.commit()
    queue.reap_stale(session, timeout_s=300, max_attempts=3)
    session.execute(
        update(Job).values(status="running", attempts=3, locked_at=func.now() - timedelta(hours=1))
    )
    session.commit()
    queue.reap_stale(session, timeout_s=300, max_attempts=3)
    session.commit()
    types = session.execute(
        text(
            "select event_type from audit_events where tenant_id=:t "
            "and actor_id='reaper' order by id"
        ),
        {"t": tenant_id},
    ).scalars().all()  # fmt: skip
    assert types == ["job_requeued_after_lost_worker", "job_failed"]
    assert session.scalar(text("select last_error from jobs")) == "WorkerLost"


def test_a_worker_that_lost_its_lock_cannot_record_a_result(
    session: Session, tenant_id: uuid.UUID
) -> None:
    add_job(session, tenant_id)
    job = queue.claim(session)
    assert job is not None
    attempt = job.attempts
    session.commit()
    # the reaper re-queues it and a second worker claims it (attempt 2)
    session.execute(update(Job).values(locked_at=func.now() - timedelta(hours=1)))
    session.commit()
    queue.reap_stale(session, timeout_s=300, max_attempts=3)
    session.commit()
    second = queue.claim(session)
    assert second is not None and second.attempts == 2
    session.commit()
    # the slow first worker now tries to finish: it must be told it no longer owns the job
    assert queue.complete(session, job, attempt=attempt) is False
    assert queue.fail(session, job, "late", attempt=attempt, **KW) is None
    session.rollback()
    assert session.scalar(text("select status from jobs")) == "running"


def test_stats_can_be_scoped_to_a_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    other = Tenant(name="other")
    session.add(other)
    session.flush()
    queue.enqueue(session, tenant_id=other.id, type="t", payload={})
    add_job(session, tenant_id, key="mine")
    assert queue.stats(session, timeout_s=300, tenant_id=tenant_id).counts["queued"] == 1
    assert queue.stats(session, timeout_s=300, tenant_id=other.id).counts["queued"] == 1
    session.rollback()
