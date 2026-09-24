"""Pure rules for accepting an uploaded file (playbook §6.1).

These are upload rejections, separate from the invoice exception taxonomy: a rejected file never
becomes an invoice. Detection uses file content, never the filename or the client's MIME type.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from intake.core.statuses import DocQuality

ALLOWED_MIMES = frozenset({"application/pdf", "image/png", "image/jpeg", "image/tiff"})
MIN_TEXT_CHARS = 20  # fewer characters than this is not a usable text layer
BLANK_TONE_RANGE = 15  # 0-255 grey-level spread below which a page is treated as blank


class IngestErrorCode(StrEnum):
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    EMPTY_FILE = "EMPTY_FILE"
    UNREADABLE_FILE = "UNREADABLE_FILE"


_TEMPLATES: dict[IngestErrorCode, tuple[str, str]] = {
    IngestErrorCode.UNSUPPORTED_FILE_TYPE: (
        "This file type is not supported. Upload a PDF, PNG, JPG or TIFF.",
        "Convert the file to PDF, PNG, JPG or TIFF and upload it again",
    ),
    IngestErrorCode.FILE_TOO_LARGE: (
        "The file is larger than the {max_mb} MB limit.",
        "Compress or split the file, or scan at a lower resolution",
    ),
    IngestErrorCode.TOO_MANY_PAGES: (
        "The document has {pages} pages; the limit is {max_pages}.",
        "Upload only the invoice pages, or split the file",
    ),
    IngestErrorCode.EMPTY_FILE: (
        "The file is empty.",
        "Check the file and upload it again",
    ),
    IngestErrorCode.UNREADABLE_FILE: (
        "The file could not be opened. It may be corrupted or password protected.",
        "Re-export or re-scan the document and upload it again",
    ),
}


@dataclass(frozen=True)
class UploadRejection:
    code: IngestErrorCode
    message: str
    fix: str


def rejection(
    code: IngestErrorCode, *, max_bytes: int = 0, max_pages: int = 0, pages: int = 0
) -> UploadRejection:
    if code is IngestErrorCode.FILE_TOO_LARGE and max_bytes <= 0:
        raise ValueError("max_bytes is required for FILE_TOO_LARGE")
    if code is IngestErrorCode.TOO_MANY_PAGES and max_pages <= 0:
        raise ValueError("max_pages is required for TOO_MANY_PAGES")
    message, fix = _TEMPLATES[code]
    params = {"max_mb": f"{max_bytes / (1024 * 1024):g}", "max_pages": max_pages, "pages": pages}
    return UploadRejection(code, message.format(**params), fix.format(**params))


def detect_mime(head: bytes) -> str | None:
    """Identify an allowed file type from its first bytes. None if it is not an allowed type."""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    return None


def check_size_and_type(
    size: int, head: bytes, *, max_bytes: int
) -> tuple[str | None, UploadRejection | None]:
    """First-stage checks that need no parsing. Returns (mime, rejection)."""
    if size <= 0:
        return None, rejection(IngestErrorCode.EMPTY_FILE)
    if size > max_bytes:
        return None, rejection(IngestErrorCode.FILE_TOO_LARGE, max_bytes=max_bytes)
    mime = detect_mime(head)
    if mime is None:
        return None, rejection(IngestErrorCode.UNSUPPORTED_FILE_TYPE)
    return mime, None


def check_pages(pages: int, *, max_pages: int) -> UploadRejection | None:
    """Second stage, once the page count is known. A document with no pages is unreadable."""
    if pages <= 0:
        return rejection(IngestErrorCode.UNREADABLE_FILE)
    if pages > max_pages:
        return rejection(IngestErrorCode.TOO_MANY_PAGES, max_pages=max_pages, pages=pages)
    return None


def text_layer_chars(page_texts: Sequence[str]) -> int:
    """Characters of real text across pages; whitespace does not count."""
    return sum(len(t.strip()) for t in page_texts)


def is_blank(*, tone_range: float) -> bool:
    return tone_range < BLANK_TONE_RANGE


def classify_doc_quality(mime: str, *, text_chars: int, blank: bool) -> DocQuality:
    """Heuristic from the playbook: text layer -> clean, image-only PDF -> scanned,
    image upload -> photo. A blank page is unknown. A real text layer wins over blankness.
    Any non-PDF type is an image here: `detect_mime` only lets allowed types through."""
    if mime == "application/pdf" and text_chars >= MIN_TEXT_CHARS:
        return DocQuality.CLEAN
    if blank:
        return DocQuality.UNKNOWN
    if mime == "application/pdf":
        return DocQuality.SCANNED
    return DocQuality.PHOTO


def sanitize_filename(name: str, *, max_len: int = 200) -> str:
    """Keep only a safe display name: no directories, no control characters."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*').strip(" .")
    return cleaned[:max_len] or "upload"
