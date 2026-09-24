"""Accept one file: validate, dedupe by SHA-256, store, and create document, invoice and job.

The caller owns the transaction (commit after success). Every accepted file writes
`document_received` and `status_changed`; a repeat writes `duplicate_file_upload`.
"""

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.config import Settings
from intake.core.ingest import (
    IngestErrorCode,
    check_pages,
    check_size_and_type,
    rejection,
    sanitize_filename,
)
from intake.core.statuses import ActorType, DocQuality, InvoiceStatus
from intake.db.models import Document, Invoice, Tenant
from intake.extract.pages import UnreadableFile, count_pages
from intake.ingest.base import IngestRejected, IngestResult
from intake.ingest.storage import LocalStorage
from intake.worker import queue

PROCESS_JOB = "process_document"


class UploadIngestor:
    """The default Ingestor, used by the API and the folder watcher."""

    def __init__(
        self, settings: Settings, storage: LocalStorage, *, actor_id: str = "upload"
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.actor_id = actor_id
        self.tenant_id = uuid.UUID(settings.default_tenant_id)

    def ingest(
        self, session: Session, *, content: bytes, filename: str, source: str
    ) -> IngestResult:
        cfg = self.settings
        mime, rej = check_size_and_type(len(content), content[:16], max_bytes=cfg.max_upload_bytes)
        if rej or mime is None:
            raise IngestRejected(rej or rejection(IngestErrorCode.UNSUPPORTED_FILE_TYPE))
        try:
            pages = count_pages(content, mime, max_image_pixels=cfg.max_image_pixels)
        except UnreadableFile:
            raise IngestRejected(rejection(IngestErrorCode.UNREADABLE_FILE)) from None
        rej = check_pages(pages, max_pages=cfg.max_pages)
        if rej:
            raise IngestRejected(rej)

        sha = hashlib.sha256(content).hexdigest()
        safe_name = sanitize_filename(filename)
        self._ensure_tenant(session)

        existing = self._find(session, sha)
        if existing is None:
            try:
                with session.begin_nested():
                    return self._create(session, content, sha, mime, pages, safe_name, source)
            except IntegrityError:  # a concurrent upload of the same file won the race
                existing = self._find(session, sha)
                if existing is None:
                    raise
        return self._duplicate(session, existing, safe_name, source)

    # -- helpers
    def _ensure_tenant(self, session: Session) -> None:
        session.execute(
            insert(Tenant)
            .values(id=self.tenant_id, name="Default tenant")
            .on_conflict_do_nothing(index_elements=["id"])
        )

    def _find(self, session: Session, sha: str) -> Document | None:
        return session.execute(
            select(Document).where(
                Document.tenant_id == self.tenant_id, Document.file_sha256 == sha
            )
        ).scalar_one_or_none()

    def _create(
        self, session: Session, content: bytes, sha: str, mime: str, pages: int,
        filename: str, source: str,
    ) -> IngestResult:  # fmt: skip
        rel = self.storage.original_rel(self.tenant_id, sha)
        self.storage.put(rel, content)
        doc = Document(
            tenant_id=self.tenant_id, file_sha256=sha, filename=filename, mime=mime,
            page_count=pages, storage_path=rel, source=source,
            doc_quality=DocQuality.UNKNOWN.value,
        )  # fmt: skip
        session.add(doc)
        session.flush()
        inv = Invoice(
            tenant_id=self.tenant_id, document_id=doc.id, status=InvoiceStatus.RECEIVED.value
        )
        session.add(inv)
        session.flush()
        job = queue.enqueue(
            session,
            tenant_id=self.tenant_id,
            type=PROCESS_JOB,
            payload={"document_id": str(doc.id), "invoice_id": str(inv.id)},
            dedupe_key=f"{PROCESS_JOB}:{doc.id}",
        )
        record_event(
            session, tenant_id=self.tenant_id, invoice_id=inv.id, event_type="document_received",
            actor_type=ActorType.SYSTEM, actor_id=self.actor_id,
            data={"document_id": str(doc.id), "sha256": sha, "mime": mime, "size": len(content),
                  "pages": pages, "filename": filename, "source": source},
        )  # fmt: skip
        record_event(
            session, tenant_id=self.tenant_id, invoice_id=inv.id, event_type="status_changed",
            actor_type=ActorType.SYSTEM, actor_id=self.actor_id,
            data={"from": None, "to": InvoiceStatus.RECEIVED.value},
        )  # fmt: skip
        return IngestResult(doc.id, inv.id, job.id, duplicate=False)

    def _duplicate(
        self, session: Session, doc: Document, filename: str, source: str
    ) -> IngestResult:
        inv = session.execute(select(Invoice).where(Invoice.document_id == doc.id)).scalar_one()
        record_event(
            session, tenant_id=self.tenant_id, invoice_id=inv.id,
            event_type="duplicate_file_upload", actor_type=ActorType.SYSTEM, actor_id=self.actor_id,
            data={"document_id": str(doc.id), "filename": filename, "source": source},
        )  # fmt: skip
        return IngestResult(doc.id, inv.id, None, duplicate=True)
