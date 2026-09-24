"""Local file storage for originals and rendered pages.

Paths are built only from validated hex hashes and UUIDs, never from client-supplied names.
"""

import os
import re
import uuid
from pathlib import Path

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class LocalStorage:
    def __init__(self, base_dir: str | Path) -> None:
        self.base = Path(base_dir).resolve()

    def _safe(self, rel: str) -> Path:
        path = (self.base / rel).resolve()
        if not path.is_relative_to(self.base):
            raise ValueError("path escapes the storage directory")
        return path

    @staticmethod
    def original_rel(tenant_id: uuid.UUID, sha256: str) -> str:
        if not _HEX64.match(sha256):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return f"{tenant_id}/originals/{sha256[:2]}/{sha256}"

    @staticmethod
    def pages_rel(tenant_id: uuid.UUID, document_id: uuid.UUID) -> str:
        return f"{tenant_id}/pages/{document_id}"

    def put(self, rel: str, content: bytes) -> None:
        """Write atomically; an existing file with the same content-addressed name is kept."""
        path = self._safe(rel)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(content)
        os.replace(tmp, path)

    def overwrite(self, rel: str, content: bytes) -> None:
        path = self._safe(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(content)
        os.replace(tmp, path)

    def read(self, rel: str) -> bytes:
        return self._safe(rel).read_bytes()

    def exists(self, rel: str) -> bool:
        return self._safe(rel).exists()
