"""The worker's `process_document` step: render pages, read the text layer, classify quality.

Idempotent: running it twice leaves the same files and values, and audits only the first time.
"""

import io
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.config import Settings
from intake.core.ingest import classify_doc_quality, is_blank, text_layer_chars
from intake.core.statuses import ActorType
from intake.db.models import AuditEvent, Document, Invoice
from intake.extract.pages import render_pages
from intake.ingest.storage import LocalStorage


def process_document(
    session: Session, storage: LocalStorage, settings: Settings, document_id: uuid.UUID
) -> None:
    doc = session.get_one(Document, document_id)
    inv = session.execute(select(Invoice).where(Invoice.document_id == doc.id)).scalar_one()
    base = storage.pages_rel(doc.tenant_id, doc.id)
    texts: list[str] = []
    tone: float | None = None
    for n, page in enumerate(
        render_pages(
            storage.read(doc.storage_path), doc.mime,
            dpi=settings.render_dpi, max_pages=settings.max_pages,
            max_image_pixels=settings.max_image_pixels,
        ),
        start=1,
    ):  # fmt: skip
        if n == 1:
            tone = page.tone_range
        buf = io.BytesIO()
        page.image.save(buf, "PNG")
        storage.overwrite(f"{base}/page-{n:03d}.png", buf.getvalue())
        texts.append(page.text)
        page.image.close()  # one full-size page in memory at a time
    storage.overwrite(f"{base}/text.json", json.dumps(texts).encode())

    text_chars = text_layer_chars(texts)
    quality = classify_doc_quality(
        doc.mime,
        text_chars=text_chars,
        blank=is_blank(tone_range=tone if tone is not None else 0.0),
    )
    doc.page_count = len(texts)
    doc.doc_quality = quality.value

    already = session.execute(
        select(AuditEvent.id).where(
            AuditEvent.invoice_id == inv.id, AuditEvent.event_type == "document_processed"
        )
    ).first()
    if already is None:
        record_event(
            session, tenant_id=doc.tenant_id, invoice_id=inv.id, event_type="document_processed",
            actor_type=ActorType.SYSTEM, actor_id="worker",
            data={"document_id": str(doc.id), "pages": len(texts),
                  "doc_quality": quality.value, "text_chars": text_chars},
        )  # fmt: skip
