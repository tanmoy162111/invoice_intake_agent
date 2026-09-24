import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from intake.core.ingest import UploadRejection


class IngestRejected(Exception):
    """The file was refused; nothing was stored."""

    def __init__(self, rejection: UploadRejection) -> None:
        super().__init__(rejection.message)
        self.rejection = rejection


@dataclass(frozen=True)
class IngestResult:
    document_id: uuid.UUID
    invoice_id: uuid.UUID
    job_id: uuid.UUID | None  # None when the file was already known
    duplicate: bool


class Ingestor(Protocol):
    """A way documents enter the system: web upload, watched folder, email, ..."""

    def ingest(
        self, session: Session, *, content: bytes, filename: str, source: str
    ) -> IngestResult: ...
