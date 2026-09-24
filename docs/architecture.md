# Architecture notes

The overall design is in [`playbook.md`](playbook.md) §3. This page records what is built.

## Data model (M1)

All 17 tables from playbook §5.1 exist (migrations `0002` and `0003`).

- **Tenancy:** every business table has a non-null `tenant_id`. `eval_runs` is global. A test
  fails if a new table is added without one.
- **Money:** integer minor units plus an ISO currency code (`*_minor` columns, `char(3)` currency).
  Parsing and arithmetic helpers are in `core/money.py`. No floats anywhere.
- **Closed vocabularies** (statuses, routes, actor types, exception codes, ...) are enums in
  `core/` and are enforced in Postgres with `CHECK` constraints.
- **Audit log:** `audit_events` is append-only. A trigger rejects `UPDATE`, `DELETE` and
  `TRUNCATE` (migration `0003`), and the only writer is `audit/writer.py::record_event`.
- **Bank details:** stored encrypted (Fernet, key `BANK_ENCRYPTION_KEY`) in
  `suppliers.bank_account_encrypted`; comparisons use `bank_account_hash`, a keyed HMAC of the
  normalised account number, so plaintext is never needed to compare. `security.BankVault`.
- **Migrations are immutable** once committed: add a new revision instead of editing one.

## Synthetic data (M1)

`data/generator/` builds a deterministic demo world (fixed seed): 15 suppliers, 104 POs with
receipts, and 120 invoices rendered in 5 layouts and 3 qualities (clean PDF, scanned, photo),
with planted problems covering every exception code. See `data/seed/MANIFEST.md`.

- `make generate` rebuilds `data/seed/` and `data/golden/`. Output is deterministic.
- `data/seed/truth/*.json` is the ground truth for each document. `expected.must_raise` are the
  planted problems; `may_raise` are acceptable knock-on codes (`*` = anything, unreadable file).
- The golden set (60 invoices, chosen by a fixed rule) is copied to `data/golden/` once and never
  overwritten. Do not use it while writing prompts.
- Conventions the truth follows, which validation and matching must implement the same way:
  PO totals are net of tax and compared with the invoice subtotal; the approval limit applies to
  the invoice total including tax; a document only carries what is printed on it (a field a layout
  does not print is `null` in the truth).

`make seed` loads suppliers, POs and receipts into Postgres (idempotent, audit-logged) and copies
the invoice files (not the truth) to `data/inbox/`, ready to upload once ingestion exists (M2).

## Ingestion and job queue (M2)

```
POST /documents ──▶ validate (type by content, size, pages) ──▶ SHA-256 ──▶ store original
                       │ reject: 413/415/422 + code, message, fix          (storage/<tenant>/originals/aa/<sha>)
                       ▼
        one transaction: documents + invoices(received) + jobs(process_document) + audit events
                       ▼
Worker: claim (SKIP LOCKED) ─▶ render pages (200 DPI PNG) + text layer ─▶ classify doc_quality ─▶ audit
```

- **One path in:** `ingest/service.py::UploadIngestor` implements the `Ingestor` interface. The API
  and `FolderIngestor` (`make ingest-inbox`) both use it, so validation and dedupe are identical.
- **Duplicate files:** same SHA-256 for the tenant returns the existing document (HTTP 200,
  `duplicate: true`), writes `duplicate_file_upload`, and creates nothing. A unique constraint plus
  a savepoint makes concurrent uploads of the same file safe. This is separate from the duplicate
  *invoice* check (M5).
- **Upload rejections** (`core/ingest.py`): `UNSUPPORTED_FILE_TYPE` (415), `FILE_TOO_LARGE` (413),
  `TOO_MANY_PAGES`, `EMPTY_FILE`, `UNREADABLE_FILE` (422). These are not invoice exceptions: a
  rejected file never becomes an invoice. A file that opens but is illegible is accepted and later
  raises the invoice exception `UNREADABLE_DOCUMENT` (M3+).
- **Queue** (`worker/queue.py`): Postgres rows claimed with `FOR UPDATE SKIP LOCKED`; failures retry
  with exponential backoff (`JOB_BACKOFF_*`) up to `JOB_MAX_ATTEMPTS`, then `failed`. A reaper
  re-queues jobs whose worker died (`JOB_VISIBILITY_TIMEOUT_S`). `jobs.dedupe_key` makes enqueue
  idempotent. `GET /jobs` shows counts, stuck jobs and recent failures.
- **Handlers are idempotent:** re-running `process_document` rewrites the same files and audits once.
- **doc_quality heuristic** (`core/ingest.py::classify_doc_quality`): PDF with a text layer -> `clean`;
  image-only PDF -> `scanned`; image upload -> `photo`; blank page -> `unknown`. On the 120 synthetic
  documents it matches the ground truth 120/120 (`make ingest-inbox`, then compare with `data/seed/truth`).
- **Status changes** go through `db/invoices.py::set_invoice_status`, which enforces the transition
  table in `core/workflow.py` and writes `status_changed`.
- **Auth (interim):** every route except `/health` needs `Authorization: Bearer $API_TOKEN` (constant-time
  compare). With no token configured the server refuses (fail closed). `/docs` and `/redoc` are off;
  `/openapi.json` needs the token. Real login replaces this in M8.
- **Upload guard** (`api/guard.py`): FastAPI parses a multipart body before route dependencies run, so a
  small ASGI guard checks the token, a required `Content-Length` (411) and the size limit (413) first.
  An unauthenticated client cannot make the server buffer a large upload.
- **Hostile files:** pages render one at a time, every image frame is checked against `MAX_IMAGE_PIXELS`
  (25M), PDF page renders are scaled down to that cap, only PNG/JPEG/TIFF are opened, and the worker
  container has a memory limit. Not yet done: running parsers in a sandboxed subprocess with a
  wall-clock timeout (see the PR notes).
- **Job failures record only the exception class** (`jobs.last_error`, audit events, logs), because
  messages could echo document content. Detail is available at DEBUG level only.
- **Lost workers:** the reaper re-queues (or fails) jobs whose lock expired using the same retry rule as
  normal failures, and writes an audit event. A worker that finishes after losing its lock cannot record
  a result (the claim is fenced by status and attempt number).
