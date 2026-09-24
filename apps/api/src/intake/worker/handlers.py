import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.config import Settings
from intake.core.statuses import ActorType
from intake.db.models import Job
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
        return None

    return handler


HANDLERS: dict[str, Handler] = {
    PROCESS_JOB: _process_document,
    EXTRACT_JOB: make_extract_handler(),
}
