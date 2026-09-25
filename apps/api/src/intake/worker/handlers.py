import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.checks.duplicates import DEDUPE_JOB, detect_duplicates
from intake.checks.matching import MATCH_JOB, match_invoice
from intake.checks.pipeline import VALIDATE_JOB, validate_invoice
from intake.checks.routing import ROUTE_JOB, route_invoice
from intake.config import Settings
from intake.core.statuses import ActorType, InvoiceStatus
from intake.db.models import Invoice, Job
from intake.extract.factory import build_client
from intake.extract.llm import LlmClient
from intake.extract.pipeline import EXTRACT_JOB, extract_invoice
from intake.extract.service import SpendCapReached, db_now
from intake.ingest.processing import process_document
from intake.ingest.service import PROCESS_JOB
from intake.ingest.storage import LocalStorage
from intake.security import BankVault
from intake.worker import queue

Handler = Callable[[Session, LocalStorage, Settings, Job], queue.Deferral | None]
ClientFactory = Callable[[Settings], LlmClient | None]


def _process_document(
    session: Session, storage: LocalStorage, settings: Settings, job: Job
) -> None:
    process_document(session, storage, settings, uuid.UUID(job.payload["document_id"]))
    queue.enqueue(
        session,
        tenant_id=job.tenant_id,
        type=EXTRACT_JOB,
        payload={
            "invoice_id": job.payload["invoice_id"],
            "document_id": job.payload["document_id"],
        },
        dedupe_key=f"{EXTRACT_JOB}:{job.payload['invoice_id']}",
    )


def make_extract_handler(client_factory: ClientFactory = build_client) -> Handler:
    def handler(
        session: Session, storage: LocalStorage, settings: Settings, job: Job
    ) -> queue.Deferral | None:
        client = client_factory(settings)
        try:
            vault: BankVault | None = BankVault(settings.bank_encryption_key)
        except ValueError:
            vault = None
        if client is None or vault is None:
            until = db_now(session) + timedelta(seconds=settings.extract_not_configured_retry_s)
            return queue.Deferral(until, "EXTRACTION_NOT_CONFIGURED")
        invoice_id = uuid.UUID(job.payload["invoice_id"])
        try:
            extract_invoice(session, storage, settings, client, vault, invoice_id)
        except SpendCapReached as paused:
            if job.last_error != "SPEND_CAP_REACHED":  # one audit event per pause, not per poll
                record_event(
                    session, tenant_id=job.tenant_id, invoice_id=invoice_id,
                    event_type="extraction_paused", actor_type=ActorType.SYSTEM,
                    actor_id="worker",
                    data={"reason": "SPEND_CAP_REACHED", "resume_at": paused.resume_at.isoformat()},
                )  # fmt: skip
            return queue.Deferral(paused.resume_at, "SPEND_CAP_REACHED")
        inv = session.get_one(Invoice, invoice_id)
        if InvoiceStatus(inv.status) is InvoiceStatus.EXTRACTED:  # next stage: the checks
            queue.enqueue(
                session, tenant_id=job.tenant_id, type=VALIDATE_JOB,
                payload={"invoice_id": str(invoice_id)}, dedupe_key=f"{VALIDATE_JOB}:{invoice_id}",
            )  # fmt: skip
        return None

    return handler


def _validate_invoice(
    session: Session, storage: LocalStorage, settings: Settings, job: Job
) -> None:
    invoice_id = uuid.UUID(job.payload["invoice_id"])
    validate_invoice(session, settings, invoice_id, tenant_id=job.tenant_id)
    inv = session.get_one(Invoice, invoice_id)
    if InvoiceStatus(inv.status) is InvoiceStatus.CHECKING:  # next stage: duplicates
        queue.enqueue(
            session, tenant_id=job.tenant_id, type=DEDUPE_JOB,
            payload={"invoice_id": str(invoice_id)}, dedupe_key=f"{DEDUPE_JOB}:{invoice_id}",
        )  # fmt: skip


def _detect_duplicates(
    session: Session, storage: LocalStorage, settings: Settings, job: Job
) -> queue.Deferral | None:
    waited = db_now(session) - job.created_at
    invoice_id = uuid.UUID(job.payload["invoice_id"])
    deferral = detect_duplicates(
        session, settings, invoice_id, tenant_id=job.tenant_id,
        stop_waiting=waited > timedelta(seconds=settings.dedupe_max_wait_s),
    )  # fmt: skip
    if deferral is None:  # next stage: the 3-way match
        queue.enqueue(
            session, tenant_id=job.tenant_id, type=MATCH_JOB,
            payload={"invoice_id": str(invoice_id)}, dedupe_key=f"{MATCH_JOB}:{invoice_id}",
        )  # fmt: skip
    return deferral


def _match_invoice(
    session: Session, storage: LocalStorage, settings: Settings, job: Job
) -> queue.Deferral | None:
    waited = db_now(session) - job.created_at
    invoice_id = uuid.UUID(job.payload["invoice_id"])
    deferral = match_invoice(
        session, settings, invoice_id, tenant_id=job.tenant_id,
        stop_waiting=waited > timedelta(seconds=settings.match_max_wait_s),
    )  # fmt: skip
    if deferral is None:  # next stage: exceptions and routing
        queue.enqueue(
            session, tenant_id=job.tenant_id, type=ROUTE_JOB,
            payload={"invoice_id": str(invoice_id)}, dedupe_key=f"{ROUTE_JOB}:{invoice_id}",
        )  # fmt: skip
    return deferral


def _route_invoice(session: Session, storage: LocalStorage, settings: Settings, job: Job) -> None:
    route_invoice(session, settings, uuid.UUID(job.payload["invoice_id"]), tenant_id=job.tenant_id)


HANDLERS: dict[str, Handler] = {
    PROCESS_JOB: _process_document,
    EXTRACT_JOB: make_extract_handler(),
    VALIDATE_JOB: _validate_invoice,
    DEDUPE_JOB: _detect_duplicates,
    MATCH_JOB: _match_invoice,
    ROUTE_JOB: _route_invoice,
}
