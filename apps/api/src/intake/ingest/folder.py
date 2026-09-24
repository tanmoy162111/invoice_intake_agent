"""FolderIngestor: feed every file in a directory (the demo inbox) through the same ingest path.

Files are not moved or deleted, and re-running is safe because uploads are deduplicated by hash.
    python -m intake.ingest.folder [--inbox DIR]
"""

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from intake.config import get_settings
from intake.core.ingest import IngestErrorCode
from intake.db.session import make_engine
from intake.ingest.base import Ingestor, IngestRejected
from intake.ingest.service import UploadIngestor
from intake.ingest.storage import LocalStorage


@dataclass
class FolderReport:
    accepted: int = 0
    duplicates: int = 0
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (filename, error code)


class FolderIngestor:
    def __init__(self, ingestor: Ingestor, folder: Path, *, max_bytes: int | None = None) -> None:
        self.ingestor = ingestor
        self.folder = folder
        self.max_bytes = max_bytes

    def run(self, engine: Engine) -> FolderReport:
        report = FolderReport()
        files = [
            p
            for p in self.folder.iterdir()
            if p.is_file() and not p.is_symlink() and not p.name.startswith(".")
        ]
        for path in sorted(files):
            if self.max_bytes is not None and path.stat().st_size > self.max_bytes:
                report.rejected.append((path.name, IngestErrorCode.FILE_TOO_LARGE.value))
                continue  # never read an oversized file into memory
            with Session(engine) as session:
                try:
                    result = self.ingestor.ingest(
                        session, content=path.read_bytes(), filename=path.name, source="folder"
                    )
                    session.commit()
                except IngestRejected as exc:
                    session.rollback()
                    report.rejected.append((path.name, exc.rejection.code.value))
                    continue
            if result.duplicate:
                report.duplicates += 1
            else:
                report.accepted += 1
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox", type=Path, default=Path("../../data/inbox"))
    args = parser.parse_args()
    settings = get_settings()
    ingestor = UploadIngestor(settings, LocalStorage(settings.storage_dir), actor_id="folder")
    report = FolderIngestor(ingestor, args.inbox, max_bytes=settings.max_upload_bytes).run(
        make_engine()
    )
    print(
        f"accepted {report.accepted}, already known {report.duplicates}, "
        f"rejected {len(report.rejected)}"
    )
    for name, code in report.rejected:
        print(f"  rejected {name}: {code}")


if __name__ == "__main__":
    main()
