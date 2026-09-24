import pytest

from intake.core.ingest import (
    IngestErrorCode,
    check_pages,
    check_size_and_type,
    classify_doc_quality,
    detect_mime,
    is_blank,
    rejection,
)
from intake.core.statuses import DocQuality

PDF = b"%PDF-1.7\n%..."
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 8
JPG = b"\xff\xd8\xff\xe0" + b"0" * 8
TIFF_LE = b"II*\x00" + b"0" * 8
TIFF_BE = b"MM\x00*" + b"0" * 8


@pytest.mark.parametrize(
    ("head", "mime"),
    [
        (PDF, "application/pdf"),
        (PNG, "image/png"),
        (JPG, "image/jpeg"),
        (TIFF_LE, "image/tiff"),
        (TIFF_BE, "image/tiff"),
        (b"<html><script>alert(1)</script>", None),
        (b"PK\x03\x04zip", None),
        (b"MZ\x90\x00", None),
        (b"", None),
    ],
)
def test_detect_mime_uses_content_not_name(head: bytes, mime: str | None) -> None:
    assert detect_mime(head) == mime


def test_empty_file_is_rejected() -> None:
    mime, rej = check_size_and_type(0, b"", max_bytes=100)
    assert mime is None and rej is not None and rej.code is IngestErrorCode.EMPTY_FILE


def test_too_large_is_rejected_before_type() -> None:
    limit = 15 * 1024 * 1024
    _, rej = check_size_and_type(limit + 1, b"<html>", max_bytes=limit)
    assert rej is not None and rej.code is IngestErrorCode.FILE_TOO_LARGE
    assert "15 MB" in rej.message


def test_unsupported_type_is_rejected() -> None:
    _, rej = check_size_and_type(50, b"<html>", max_bytes=100)
    assert rej is not None and rej.code is IngestErrorCode.UNSUPPORTED_FILE_TYPE


def test_valid_upload_passes() -> None:
    assert check_size_and_type(50, PDF, max_bytes=100) == ("application/pdf", None)


def test_size_limit_is_inclusive() -> None:
    assert check_size_and_type(100, PDF, max_bytes=100)[1] is None


def test_page_limit() -> None:
    assert check_pages(10, max_pages=10) is None
    rej = check_pages(11, max_pages=10)
    assert rej is not None and rej.code is IngestErrorCode.TOO_MANY_PAGES
    assert check_pages(0, max_pages=10) is not None  # a document with no pages is unreadable


@pytest.mark.parametrize("code", list(IngestErrorCode))
def test_every_error_code_has_message_and_fix(code: IngestErrorCode) -> None:
    rej = rejection(code, max_bytes=15 * 1024 * 1024, max_pages=10, pages=12)
    assert rej.message.strip() and rej.fix.strip()
    assert "{" not in rej.message and "{" not in rej.fix


def test_codes_are_upper_snake_case() -> None:
    assert all(c.value == c.name for c in IngestErrorCode)


@pytest.mark.parametrize(
    ("mime", "chars", "blank", "expected"),
    [
        ("application/pdf", 400, False, DocQuality.CLEAN),
        ("application/pdf", 0, False, DocQuality.SCANNED),
        ("application/pdf", 5, False, DocQuality.SCANNED),  # stray characters are not a text layer
        ("image/jpeg", 0, False, DocQuality.PHOTO),
        ("image/png", 0, False, DocQuality.PHOTO),
        ("image/tiff", 0, False, DocQuality.PHOTO),
        ("application/pdf", 0, True, DocQuality.UNKNOWN),
        ("image/jpeg", 0, True, DocQuality.UNKNOWN),
        ("application/pdf", 400, True, DocQuality.CLEAN),  # a real text layer wins over blankness
    ],
)
def test_classify_doc_quality(mime: str, chars: int, blank: bool, expected: DocQuality) -> None:
    assert classify_doc_quality(mime, text_chars=chars, blank=blank) is expected


def test_is_blank_uses_tone_range() -> None:
    assert is_blank(tone_range=3)
    assert not is_blank(tone_range=60)
