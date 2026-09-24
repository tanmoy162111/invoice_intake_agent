import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from intake.config import Settings
from intake.db.models import Job
from intake.ingest.processing import process_document
from intake.ingest.service import PROCESS_JOB
from intake.ingest.storage import LocalStorage

Handler = Callable[[Session, LocalStorage, Settings, Job], None]


def _process_document(
    session: Session, storage: LocalStorage, settings: Settings, job: Job
) -> None:
    process_document(session, storage, settings, uuid.UUID(job.payload["document_id"]))


HANDLERS: dict[str, Handler] = {PROCESS_JOB: _process_document}
