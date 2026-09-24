import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from intake.api.deps import ingestor_dep, session_dep, settings_dep
from intake.config import Settings
from intake.core.ingest import IngestErrorCode
from intake.db.models import Document, Invoice
from intake.ingest.base import IngestRejected
from intake.ingest.service import UploadIngestor

router = APIRouter(prefix="/documents", tags=["documents"])

REJECTION_STATUS = {
    IngestErrorCode.UNSUPPORTED_FILE_TYPE: 415,
    IngestErrorCode.FILE_TOO_LARGE: 413,
    IngestErrorCode.TOO_MANY_PAGES: 422,
    IngestErrorCode.EMPTY_FILE: 422,
    IngestErrorCode.UNREADABLE_FILE: 422,
}


class DocumentOut(BaseModel):
    document_id: uuid.UUID
    invoice_id: uuid.UUID
    job_id: uuid.UUID | None
    duplicate: bool
    filename: str
    mime: str
    page_count: int | None
    doc_quality: str
    invoice_status: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    fix: str


def _out(
    session: Session, doc: Document, *, job_id: uuid.UUID | None, duplicate: bool
) -> DocumentOut:
    inv = session.execute(select(Invoice).where(Invoice.document_id == doc.id)).scalar_one()
    return DocumentOut(
        document_id=doc.id, invoice_id=inv.id, job_id=job_id, duplicate=duplicate,
        filename=doc.filename, mime=doc.mime, page_count=doc.page_count,
        doc_quality=doc.doc_quality, invoice_status=inv.status,
    )  # fmt: skip


@router.post(
    "",
    status_code=201,
    response_model=DocumentOut,
    responses={
        200: {"model": DocumentOut, "description": "This exact file was already uploaded"},
        413: {"model": ErrorDetail}, 415: {"model": ErrorDetail}, 422: {"model": ErrorDetail},
    },
)  # fmt: skip
def upload_document(
    response: Response,
    file: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    ingestor: Annotated[UploadIngestor, Depends(ingestor_dep)],
) -> DocumentOut:
    # UploadGuard has already bounded the request; still read at most limit + 1 bytes.
    content = file.file.read(settings.max_upload_bytes + 1)
    try:
        result = ingestor.ingest(
            session, content=content, filename=file.filename or "upload", source="upload"
        )
    except IngestRejected as exc:
        rej = exc.rejection
        raise HTTPException(
            REJECTION_STATUS[rej.code],
            detail={"code": rej.code.value, "message": rej.message, "fix": rej.fix},
        ) from None
    session.commit()
    if result.duplicate:
        response.status_code = 200
    doc = session.get_one(Document, result.document_id)
    return _out(session, doc, job_id=result.job_id, duplicate=result.duplicate)


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: uuid.UUID,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> DocumentOut:
    doc = session.get(Document, document_id)
    if doc is None or str(doc.tenant_id) != settings.default_tenant_id:
        raise HTTPException(404, "document not found")
    return _out(session, doc, job_id=None, duplicate=False)
