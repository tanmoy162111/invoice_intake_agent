import io
import json
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from intake.api.deps import storage_dep
from intake.config import Settings
from intake.db.models import Document
from intake.ingest.folder import FolderIngestor
from intake.ingest.processing import process_document
from intake.ingest.service import UploadIngestor
from intake.ingest.storage import LocalStorage
from intake.main import create_app
from intake.seed import DEFAULT_SEED_DIR
from intake.worker.runner import run_once

UNSUPPORTED = "UNSUPPORTED_FILE_TYPE"
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MANIFEST = json.loads((DEFAULT_SEED_DIR / "manifest.json").read_text())


def sample(quality: str, n: int = 0) -> tuple[str, bytes]:
    rows = [r for r in MANIFEST if r["doc_quality"] == quality and not r["must_raise"]]
    name = rows[n]["file"]
    return name, (DEFAULT_SEED_DIR / "invoices" / name).read_bytes()


def make_settings(db_url: str, tmp_path: Path, **kw: Any) -> Settings:
    return Settings(
        database_url=db_url, storage_dir=str(tmp_path / "storage"), api_token=TOKEN,
        job_backoff_base_s=0, render_dpi=100, **kw,
    )  # fmt: skip


@pytest.fixture(autouse=True)
def clean_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        for t in (
            "jobs", "llm_calls", "field_extractions", "invoice_lines", "invoices", "documents",
        ):  # fmt: skip
            conn.execute(text(f"delete from {t}"))


@pytest.fixture
def settings(migrated_db_url: str, tmp_path: Path) -> Settings:
    return make_settings(migrated_db_url, tmp_path)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def storage(settings: Settings) -> LocalStorage:
    return LocalStorage(settings.storage_dir)


def upload(client: TestClient, name: str, content: bytes, mime: str = "application/pdf") -> Any:
    return client.post("/documents", files={"file": (name, content, mime)}, headers=AUTH)


def count(engine: Engine, table: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"select count(*) from {table}")).scalar() or 0)


def rows(engine: Engine) -> tuple[int, int, int]:
    """(documents, invoices, jobs)"""
    return count(engine, "documents"), count(engine, "invoices"), count(engine, "jobs")


def events(engine: Engine, invoice_id: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("select event_type from audit_events where invoice_id=:i order by id"),
            {"i": invoice_id},
        )
        return [r[0] for r in rows]


def pdf_with_pages(n: int) -> bytes:
    pages = [Image.new("RGB", (100, 100), (i * 20, 0, 0)) for i in range(n)]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:])
    return buf.getvalue()


# --- auth


def test_health_is_public_but_everything_else_needs_the_token(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/jobs").status_code == 401
    assert client.post("/documents", files={"file": ("a.pdf", b"x")}).status_code == 401
    assert client.get("/jobs", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/jobs", headers={"Authorization": "Basic " + TOKEN}).status_code == 401
    assert client.get("/jobs", headers=AUTH).status_code == 200


def test_no_configured_token_fails_closed(migrated_db_url: str, tmp_path: Path) -> None:
    s = make_settings(migrated_db_url, tmp_path).model_copy(update={"api_token": ""})
    with TestClient(create_app(s)) as c:
        assert c.get("/jobs", headers=AUTH).status_code == 503


# --- acceptance: upload creates rows and a job


def test_upload_creates_document_invoice_job_and_audit(
    client: TestClient, engine: Engine, storage: LocalStorage
) -> None:
    name, content = sample("clean")
    r = upload(client, name, content)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["duplicate"] is False and body["invoice_status"] == "received"
    assert body["mime"] == "application/pdf" and body["job_id"]
    assert rows(engine) == (
        1,
        1,
        1,
    )
    assert events(engine, body["invoice_id"]) == ["document_received", "status_changed"]
    with Session(engine) as s:
        doc = s.get_one(Document, uuid.UUID(body["document_id"]))
        assert storage.read(doc.storage_path) == content
        assert name not in doc.storage_path  # stored by hash, never by client name
    got = client.get(f"/documents/{body['document_id']}", headers=AUTH)
    assert got.status_code == 200 and got.json()["document_id"] == body["document_id"]
    assert client.get(f"/documents/{uuid.uuid4()}", headers=AUTH).status_code == 404


def test_worker_picks_up_the_job_and_processes_the_document(
    client: TestClient, engine: Engine, storage: LocalStorage, settings: Settings
) -> None:
    name, content = sample("clean")
    body = upload(client, name, content).json()
    assert run_once(engine, storage, settings) is True  # process_document
    assert run_once(engine, storage, settings) is True  # extract_invoice: no API key, so paused
    assert run_once(engine, storage, settings) is False  # nothing runnable until it resumes

    with Session(engine) as s:
        doc = s.get_one(Document, uuid.UUID(body["document_id"]))
        assert (doc.doc_quality, doc.page_count) == ("clean", 1)
        base = storage.pages_rel(doc.tenant_id, doc.id)
        assert storage.read(f"{base}/page-001.png").startswith(b"\x89PNG")
        assert json.loads(storage.read(f"{base}/text.json"))[0].strip()
    assert events(engine, body["invoice_id"]) == [
        "document_received", "status_changed", "document_processed",
    ]  # fmt: skip
    counts = client.get("/jobs", headers=AUTH).json()["counts"]
    assert (counts["done"], counts["queued"]) == (1, 1)  # the paused extract job waits
    assert (
        client.get(f"/documents/{body['document_id']}", headers=AUTH).json()["doc_quality"]
        == "clean"
    )


def test_processing_twice_is_idempotent(
    client: TestClient, engine: Engine, storage: LocalStorage, settings: Settings
) -> None:
    body = upload(client, *sample("scanned")).json()
    run_once(engine, storage, settings)
    with Session(engine) as s:
        process_document(s, storage, settings, uuid.UUID(body["document_id"]))
        s.commit()
        assert s.get_one(Document, uuid.UUID(body["document_id"])).doc_quality == "scanned"
    assert events(engine, body["invoice_id"]).count("document_processed") == 1


# --- acceptance: same file twice


def test_same_file_twice_creates_one_document(client: TestClient, engine: Engine) -> None:
    name, content = sample("clean")
    first = upload(client, name, content)
    second = upload(client, "renamed copy.pdf", content)
    assert (first.status_code, second.status_code) == (201, 200)
    a, b = first.json(), second.json()
    assert b["duplicate"] is True and b["job_id"] is None
    assert (a["document_id"], a["invoice_id"]) == (b["document_id"], b["invoice_id"])
    assert rows(engine) == (
        1,
        1,
        1,
    )
    assert events(engine, a["invoice_id"]) == [
        "document_received", "status_changed", "duplicate_file_upload",
    ]  # fmt: skip


def test_concurrent_uploads_of_the_same_file_create_one_document(
    settings: Settings, engine: Engine
) -> None:
    name, content = sample("photo")
    results: list[int] = []
    with TestClient(create_app(settings)) as c:

        def go() -> None:
            results.append(upload(c, name, content).status_code)

        threads = [threading.Thread(target=go) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert sorted(results) == [200, 200, 200, 201]
    assert (count(engine, "documents"), count(engine, "jobs")) == (1, 1)


# --- acceptance: rejections


@pytest.mark.parametrize(
    ("label", "name", "content", "status", "code"),
    [
        ("html as pdf", "invoice.pdf", b"<html><script>x</script></html>", 415, UNSUPPORTED),
        ("zip", "a.pdf", b"PK\x03\x04" + b"0" * 50, 415, UNSUPPORTED),
        ("empty", "a.pdf", b"", 422, "EMPTY_FILE"),
        ("corrupt pdf", "a.pdf", b"%PDF-1.4 this is not a pdf", 422, "UNREADABLE_FILE"),
        ("corrupt png", "a.png", b"\x89PNG\r\n\x1a\nnot really", 422, "UNREADABLE_FILE"),
        ("too many pages", "a.pdf", pdf_with_pages(11), 422, "TOO_MANY_PAGES"),
    ],
)  # fmt: skip
def test_rejected_files_get_a_clear_error_and_nothing_is_stored(
    client: TestClient, engine: Engine, storage: LocalStorage,
    label: str, name: str, content: bytes, status: int, code: str,
) -> None:  # fmt: skip
    r = upload(client, name, content)
    assert r.status_code == status, label
    detail = r.json()["detail"]
    assert detail["code"] == code and detail["message"] and detail["fix"]
    assert rows(engine) == (
        0,
        0,
        0,
    )
    assert not any(storage.base.rglob("*")) if storage.base.exists() else True


def test_oversized_file_is_rejected(migrated_db_url: str, tmp_path: Path, engine: Engine) -> None:
    s = make_settings(migrated_db_url, tmp_path, max_upload_bytes=2048)
    with TestClient(create_app(s)) as c:
        r = upload(c, "big.pdf", b"%PDF-1.4" + b"0" * 5000)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "FILE_TOO_LARGE"
    assert count(engine, "documents") == 0


def test_file_type_is_decided_by_content_not_name_or_declared_type(client: TestClient) -> None:
    _, content = sample("photo")  # a JPEG
    r = upload(client, "totally-a.pdf", content, mime="application/pdf")
    assert r.status_code == 201 and r.json()["mime"] == "image/jpeg"


def test_filename_is_sanitised_in_the_record(client: TestClient) -> None:
    _, content = sample("clean")
    r = upload(client, "../../etc/passwd.pdf", content)
    assert r.json()["filename"] == "passwd.pdf"


# --- doc quality against ground truth


@pytest.mark.parametrize("quality", ["clean", "scanned", "photo"])
def test_doc_quality_matches_ground_truth_on_samples(
    client: TestClient, engine: Engine, storage: LocalStorage, settings: Settings, quality: str
) -> None:
    for n in range(4):
        upload(client, *sample(quality, n))
    while run_once(engine, storage, settings):
        pass
    with engine.connect() as conn:
        got = {r[0] for r in conn.execute(text("select doc_quality from documents"))}
    assert got == {quality}


def test_unreadable_files_are_classified(
    client: TestClient, engine: Engine, storage: LocalStorage, settings: Settings
) -> None:
    for row in (r for r in MANIFEST if "UNREADABLE_DOCUMENT" in r["must_raise"]):
        upload(client, row["file"], (DEFAULT_SEED_DIR / "invoices" / row["file"]).read_bytes())
    while run_once(engine, storage, settings):
        pass
    with engine.connect() as conn:
        got = sorted(r[0] for r in conn.execute(text("select doc_quality from documents")))
    assert got == ["photo", "unknown"]  # blurred photo, blank page


# --- failures and retries


def test_failing_job_is_retried_then_marked_failed_with_audit(
    client: TestClient, engine: Engine, storage: LocalStorage, settings: Settings
) -> None:
    body = upload(client, *sample("clean")).json()

    def boom(*_: Any) -> None:
        raise RuntimeError("renderer exploded")

    handlers = {"process_document": boom}
    for _ in range(settings.job_max_attempts):
        assert run_once(engine, storage, settings, handlers) is True
    assert run_once(engine, storage, settings, handlers) is False  # gave up, nothing left
    ev = events(engine, body["invoice_id"])
    assert ev.count("job_retry_scheduled") == 2 and ev.count("job_failed") == 1
    overview = client.get("/jobs", headers=AUTH).json()
    assert overview["counts"]["failed"] == 1
    # only the exception class is recorded: messages could echo document content
    assert overview["failed"][0]["last_error"] == "RuntimeError"
    assert "renderer exploded" not in json.dumps(overview)
    assert overview["failed"][0]["attempts"] == 3


def test_unknown_job_type_fails_cleanly(
    engine: Engine, storage: LocalStorage, settings: Settings
) -> None:
    from intake.worker import queue

    with Session(engine) as s:
        queue.enqueue(s, tenant_id=uuid.UUID(settings.default_tenant_id), type="nope", payload={})
        s.commit()
    assert run_once(engine, storage, settings) is True
    with engine.connect() as conn:
        assert conn.execute(text("select status, last_error from jobs")).one()[0] == "queued"


# --- folder ingestor


def test_folder_ingestor_accepts_dedupes_and_rejects(
    engine: Engine, settings: Settings, storage: LocalStorage, tmp_path: Path
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.pdf").write_bytes(sample("clean", 0)[1])
    (inbox / "b.jpg").write_bytes(sample("photo", 0)[1])
    (inbox / "copy of a.pdf").write_bytes(sample("clean", 0)[1])
    (inbox / "notes.txt").write_bytes(b"not an invoice")
    (inbox / ".hidden").write_bytes(b"ignored")
    report = FolderIngestor(UploadIngestor(settings, storage, actor_id="folder"), inbox).run(engine)
    assert (report.accepted, report.duplicates) == (2, 1)
    assert report.rejected == [("notes.txt", "UNSUPPORTED_FILE_TYPE")]
    assert (count(engine, "documents"), count(engine, "jobs")) == (2, 2)
    again = FolderIngestor(UploadIngestor(settings, storage), inbox).run(engine)
    assert (again.accepted, again.duplicates) == (0, 3)


def test_storage_dependency_is_exposed() -> None:
    assert callable(storage_dep)


# --- upload guard, auth edge cases, tenant scoping


def test_guard_refuses_unauthenticated_and_oversized_requests_before_parsing(
    client: TestClient, settings: Settings
) -> None:
    boundary = {"Content-Type": "multipart/form-data; boundary=x"}
    huge = str(settings.max_upload_bytes + 10 * 1024 * 1024)
    # unauthenticated: 401, even with an absurd declared size (body is never read)
    r = client.post("/documents", content=b"", headers={**boundary, "Content-Length": huge})
    assert r.status_code == 401
    # authenticated but too large: refused from the header alone
    r = client.post("/documents", content=b"", headers={**AUTH, **boundary, "Content-Length": huge})
    assert r.status_code == 413 and r.json()["detail"]["code"] == "FILE_TOO_LARGE"


def test_guard_requires_content_length(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b"--x--"

    r = client.post(
        "/documents",
        content=chunks(),
        headers={**AUTH, "Content-Type": "multipart/form-data; boundary=x"},
    )
    assert r.status_code == 411


def test_non_ascii_token_is_a_401_not_a_500(client: TestClient) -> None:
    r = client.get("/jobs", headers={"Authorization": "Bearer t\u00f6ken".encode()})
    assert r.status_code == 401


def test_docs_and_schema_are_not_public(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 401
    schema = client.get("/openapi.json", headers=AUTH)
    assert schema.status_code == 200 and "/documents" in schema.json()["paths"]


def test_jobs_overview_only_counts_the_callers_tenant(client: TestClient, engine: Engine) -> None:
    from intake.db.models import Tenant
    from intake.worker import queue

    with Session(engine) as s:
        other = Tenant(name="other tenant")
        s.add(other)
        s.flush()
        job = queue.enqueue(s, tenant_id=other.id, type="x", payload={})
        job.status, job.last_error = "failed", "SecretError"
        s.commit()
    body = client.get("/jobs", headers=AUTH).json()
    assert body["counts"]["failed"] == 0 and body["failed"] == []


# --- folder ingestor safety


def test_folder_ingestor_skips_symlinks_and_never_reads_oversized_files(
    engine: Engine, settings: Settings, storage: LocalStorage, tmp_path: Path
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    real = tmp_path / "outside.pdf"
    real.write_bytes(sample("clean", 0)[1])
    (inbox / "link.pdf").symlink_to(real)
    (inbox / "ok.pdf").write_bytes(sample("clean", 1)[1])
    (inbox / "big.pdf").write_bytes(b"%PDF-" + b"0" * 600_000)
    ingestor = UploadIngestor(settings, storage, actor_id="folder")
    report = FolderIngestor(ingestor, inbox, max_bytes=500_000).run(engine)
    assert (report.accepted, report.duplicates) == (1, 0)
    assert report.rejected == [("big.pdf", "FILE_TOO_LARGE")]
    assert count(engine, "documents") == 1
