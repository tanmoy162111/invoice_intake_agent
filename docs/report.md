# Invoice Intake Agent: Project Report

> **Living document.** Every milestone adds its own chapter in the same pull request as the code.
> **Last updated:** after M8b-1 (the review screens), 2026-09-26.
> Companion: [`manual.md`](manual.md) explains how to *use and run* the system. This report explains
> *what was built, why, how it works, and what was proved*.

## How to read this report

Every milestone chapter has the same three layers, so anyone can read at the depth they need and
nobody gets a different story:

| Layer | Written for | Answers |
|---|---|---|
| **In plain words** | Anyone, no technical background | What did we add, and why does it matter to the business? |
| **How it works** | People who follow processes and diagrams | What happens step by step, and what are the rules? |
| **Under the hood** | Engineers | Which tables, files, endpoints and tests make it so? |

You can read only the first layer of each chapter and still understand the whole project.

---

## 1. Summary

- **What it is.** A system that receives supplier invoices (PDFs, scans, phone photos), reads them,
  checks them, and sends anything doubtful to a person. It **never pays anyone or moves money**.
- **Why.** Accounts-payable teams do not lose time typing. They lose it on *exceptions*: an amount
  that does not match the purchase order, a duplicate, goods that never arrived, a supplier whose
  bank account suddenly changed. The product is built around explaining and routing those.
- **Where we are.** Eight of fourteen milestones are built (M0 to M7 are merged) and M8 is well under way: the review API is merged and the review screens (sign in, queue, invoice, upload) are in review; acting from the browser is next.
  A file can be uploaded, safely stored, deduplicated, turned into page images, classified, and now
  *read into fields*: supplier, dates, amounts and lines, each with a confidence score, and then *checked*: does the maths add up, are the dates sane, is the supplier known, did the bank account change, and has this invoice been received before. A realistic
  demo world of 120 invoices exists to test against.
- **What is not built yet.** Acting from the browser (M8b-2), audit timeline (M9),
  accuracy report (M10), dashboard (M11), export (M12), demo polish (M13). The reading step has been
  proved end to end with recorded answers; its accuracy with a real model has **not** been measured yet.
- **Health.** 885 automated tests pass. The decision-logic code has 99.9% test coverage. Automated
  checks (CI) pass on the earlier pull requests.

### Status board

| # | Milestone | What it gives you | Status |
|---|---|---|---|
| M0 | Project skeleton | One command starts everything | Built, in review (PR #1) |
| M1 | Data model and synthetic data | A database and a messy, realistic demo world | Built, in review (PR #2) |
| M2 | Ingestion and job queue | Upload a file; it is stored and prepared | Built, in review (PR #3) |
| M3 | Extraction | Fields read from the invoice, with confidence | Built, in review (PR #4) |
| M4 | Validation rules | Math, dates, supplier and bank details checked | Built, in review |
| M5 | Duplicate detection | Repeats caught before approval | Built, merged (PR #6) |
| M6 | 3-way matching | Invoice compared with PO and receipt | Built, merged (PR #7) |
| M7 | Exceptions and routing | Plain-language explanation and next step for every problem | Built, merged (PR #8) |
| M8 | Review queue UI | A reviewer clears the queue in a browser | M8a (the API) merged (PR #9); M8b-1 (the screens) built, in review; M8b-2 (the actions and browser test) planned |
| M9 | Audit timeline | Full readable history per invoice | Planned |
| M10 | Evaluation harness | Honest, published accuracy numbers | Planned |
| M11 | Metrics dashboard | Business value at a glance | Planned |
| M12 | Export and connectors | Approved invoices leave the system | Planned |
| M13 | Demo polish | Rehearsed demo, reset button, video | Planned |

---

## 2. The problem and the idea

**In plain words.** When a supplier sends an invoice, someone has to check it against what was
ordered and what was delivered before the company pays. Most of that is routine. The expensive part is
the *exceptions*: the price is 7% higher than agreed, the same invoice arrived twice, only 80 of 100
items were delivered, the bank account on the invoice differs from the one on file. Each of those
takes phone calls and emails.

**The idea.** Read the invoice automatically, run every check a careful clerk would, and for anything
that does not add up say *exactly what is wrong, with the real numbers, and what to do about it*.
Send only those to a human. Leave the final decision, and the payment, to people.

**How it works.** Eight stages, each a small step that can be repeated safely:

```
 receive → read → tidy → validate → find duplicates → match PO/receipt → explain problems → route
   M2       M3     M3       M4            M5                M6                 M7            M7
```

**Under the hood.** A Python service (FastAPI) and a background worker share one PostgreSQL database.
A Next.js web app will be the reviewer's screen (M8). Everything runs with Docker Compose.

### Five promises the system keeps

These are enforced in code and tests, not just intentions.

1. **It never pays or moves money.** It prepares, checks, explains and routes. People approve.
2. **Every action is recorded.** Who or what did it, when, and why. The record cannot be edited or
   deleted (the database itself refuses).
3. **Uncertain means human.** A failed check or low confidence sends the invoice to a person.
   The number we watch most is the *false clear rate*: invoices wrongly passed as fine. Target: 0%.
4. **No real client data.** Only synthetic (made-up) documents live in the project.
5. **Honest accuracy.** Results are reported per field and per document quality, never as one
   flattering headline number.

---

## 3. Milestone chapters

### M0: Project skeleton

*Goal: anyone can clone the project and run it with one command.*

**In plain words.** Before building features we built the workshop: a project that starts with a single
command, checks its own quality automatically, and has a page that says "API: healthy" when the parts
are talking to each other. Boring on purpose, and it makes everything after it safer.

**How it works.** Four programs start together: a **database**, an **API** (the front desk that
receives requests), a **worker** (does background jobs), and a **web page**. The web page asks the API
if it is well; the API answers; the page shows "API: healthy". Every time code changes, an automated
robot (CI) runs style checks, type checks, and tests.

**Under the hood.**
- Repo layout follows the plan: `apps/api` (FastAPI, SQLAlchemy 2, Alembic), `apps/web` (Next.js App
  Router, strict TypeScript, Tailwind), `data/`, `eval/`, `docs/`.
- `docker-compose.yml`: `db` (Postgres 16), `api`, `worker`, `web`. Host ports are configurable
  (`API_PORT`, `WEB_PORT`, `DB_PORT`) because developer machines often have ports taken (ADR 0002).
- Web types are generated from the API's OpenAPI schema (`make gen-api`), never hand-written.
- CI (`.github/workflows/ci.yml`): ruff, mypy (strict on `core/`), pytest, eslint, tsc, vitest, web build.
- Version pins for tooling compatibility are recorded in ADR 0001 (TypeScript 5.9, ESLint 9).

**What we proved.** `docker compose up` shows the web page reading "API: healthy"; the empty first
database migration applies; CI is green on PR #1.

**Left open.** The "fresh clone works on another person's machine" check has not been done yet.

---

### M1: Data model and synthetic data

*Goal: a realistic, messy demo world exists in the database.*

**In plain words.** We designed the system's memory (the database) and built a fake but realistic
world to test against: 15 suppliers, 104 purchase orders, and 120 invoices. The invoices look like the
real thing: five different layouts, some crisp PDFs, some scans, some phone photos taken at an angle in
bad light. Crucially, we **planted known problems** in them (wrong totals, duplicates, a changed bank
account, unreadable files, and so on) and wrote down the right answer for each. That gives us an answer
key, so later we can measure honestly whether the system catches them.

**How it works.**
- The database has 17 tables covering suppliers, purchase orders, receipts, documents, invoices,
  extracted fields, check results, exceptions, reviewer actions, the audit log, model calls, background
  jobs and evaluation runs.
- Every business record carries a **tenant** (client) label, so serving many clients later needs
  configuration, not a rewrite.
- The **audit log** is insert-only: the database rejects any attempt to change or delete an entry.
- **Bank details** are stored encrypted. To compare accounts, the system uses a one-way fingerprint, so
  it never needs the plain account number.
- **Money** is stored as whole numbers of the smallest unit (cents) plus a currency code. Never as
  decimals, which cause rounding errors.
- **Exceptions** (the categories of problem) are one fixed list of 18, each with an explanation
  template and a suggested fix.
- `make seed` loads the suppliers, purchase orders and receipts. It is safe to run twice.

**Under the hood.**
- Models in `db/models.py`; migrations `0002` (17 tables, CHECK constraints for every closed
  vocabulary) and `0003` (trigger blocking `UPDATE`, `DELETE` and `TRUNCATE` on `audit_events`).
- `core/money.py` (parse `1.234,50` and `$1,234.50`, integer arithmetic), `core/exceptions.py`
  (the taxonomy, `docs/exception-taxonomy.md` is generated from it and a test keeps them in sync),
  `core/statuses.py`, `core/normalize.py`. `security.BankVault` (Fernet + keyed HMAC). `audit/writer.py`
  is the only writer to the audit log.
- Generator in `data/generator/` (`world.py` builds the deterministic world, `render.py` draws PDFs with
  reportlab then degrades them into scans and photos, `generate.py` writes files and truth).
  Fixed seed, so output is reproducible. `make generate` rebuilds it.
- Output: `data/seed/` (120 files, per-file ground truth in `truth/`, `master.json`, `MANIFEST.md`) and a
  fixed 60-invoice **golden set** in `data/golden/` (copied once, never overwritten, guarded by a hook).
- Truth conventions later checks must follow: PO totals are net of tax and compared with the invoice
  subtotal; the approval limit applies to the total including tax; `must_raise` are planted problems and
  `may_raise` acceptable knock-on codes; a field a layout does not print is `null`.

**What we proved.**

| Check | Result |
|---|---|
| Every one of the 18 exception codes appears in the dataset | Yes (test) |
| Deliberately unreadable files | Exactly 2 |
| Changed bank account | Exactly 1 |
| The audit log refuses edit, delete and truncate | Yes (tested on a real database) |
| `make seed` on an empty database, then again | 793 rows created, then 0 |
| Automated tests at this point | 119 pass |

**Left open.** Thresholds stored per client (price tolerance, approval limit) are seeded but unused
until M4.

---

### M2: Ingestion and job queue

*Goal: uploading a file creates an invoice and starts processing.*

**In plain words.** You can now hand the system a file. It checks the file is a real invoice-type
document (not a disguised program or a broken file), refuses it with a clear reason if not, recognises
if it has already seen that exact file, stores it safely, and queues it for processing. A background
worker then turns each page into an image, reads any text already embedded, and decides whether the
document is a clean digital file, a scan, or a photo. Nothing is read *into fields* yet; that is M3.

**How it works.**

```
 Upload ──▶ Checks ──▶ Already seen? ──▶ Store ──▶ Create invoice + job ──▶ Worker prepares pages
            type, size,   yes: return the           (named by its           (status: received)   images, text, quality
            pages          earlier one              fingerprint)
            no: clear reason
```

1. **Checks.** The file's type is decided by looking *inside* it, not by its name. Only PDF, PNG, JPEG
   and TIFF are accepted, up to 15 MB and 10 pages. Otherwise the upload is refused with a code, a
   message and a suggestion (for example "This file type is not supported. Convert to PDF, PNG, JPG or
   TIFF").
2. **Same file twice.** A fingerprint (SHA-256) of the file is compared with everything seen before.
   A repeat returns the existing record and logs the attempt. No second invoice is created.
3. **Background processing.** Work goes on a queue. If a job fails it is retried after waiting
   longer each time (10 s, 20 s, ...), up to three attempts, then marked failed and visible. If a worker
   crashes mid-job, a recovery step notices and retries the job.
4. **Document quality.** A PDF with real text is *clean*; a PDF that is only pictures is *scanned*; an
   image upload is a *photo*; an empty page is *unknown*. This tells later stages how much to trust the
   reading.
5. **Safety.** Uploads need a secret token; hostile files (huge images, decompression bombs) are
   bounded; errors never record document content; the audit log gets an entry for each step.

**Under the hood.**
- **API** (`api/`): `POST /documents` (201 new, 200 already known, 413/415/422 rejected),
  `GET /documents/{id}`, `GET /jobs` (queue health), `GET /health` (only public route). Bearer-token
  auth (`api/auth.py`); an ASGI `UploadGuard` (`api/guard.py`) refuses unauthenticated or oversized
  requests before the body is parsed. `/docs` is off; `/openapi.json` needs the token.
- **Pure logic** in `core/`: `ingest.py` (type detection, `IngestErrorCode` with message and fix,
  size and page checks, `classify_doc_quality`, filename sanitiser), `retry.py` (backoff),
  `workflow.py` (the allowed status changes). Written test-first.
- **Ingest** (`ingest/`): `Ingestor` protocol; `UploadIngestor` (API and folder share it, so rules are
  identical); `FolderIngestor` (`make ingest-inbox`); `LocalStorage` (path built only from a tenant id
  and a hash, never from the client's filename); `processing.py` (the worker step).
- **Rendering** (`extract/pages.py`): pypdfium2 renders at 200 DPI, pdfplumber reads the text layer.
  Pages stream one at a time; every image frame is checked against a 25-megapixel cap.
- **Queue** (`worker/queue.py`): `FOR UPDATE SKIP LOCKED` claiming, retry with backoff, an audited
  reaper reusing the same retry rule, completion fenced by status and attempt number, tenant-scoped
  stats, errors stored as the exception class only. Migration `0004` adds `jobs.dedupe_key` (one
  processing job per document) and a claim index.
- `db/invoices.py::set_invoice_status` is the single place that changes an invoice's status: it checks
  the transition table and writes the `status_changed` audit event.

**What we proved.**

| Check | Result |
|---|---|
| Upload creates document, invoice and job; worker processes it | Yes, over real HTTP against the running stack, worker in a separate container |
| Same file uploaded twice | One document (also with four simultaneous uploads) |
| Rejections: disguised HTML, zip, empty, corrupt, 11 pages, oversized | All refused with code, message and fix; nothing stored |
| Document-quality classifier vs the answer key, all 120 documents | 120 of 120 correct |
| Processing time, 120 documents | about 31 seconds |
| Two workers, 20 jobs | No job taken twice |
| Unauthenticated 20 MB upload | Refused in 2 ms without reading it |
| Automated tests | 277 pass (including 28 that keep this report and the manual consistent with the code); decision-logic coverage 100% |

Two independent reviews (decision-logic and security) ran; every confirmed finding in scope was fixed.

**Left open.**
- File parsing is bounded but not yet isolated in a sandboxed process with a time limit.
- A job that fails permanently leaves its invoice in *received*; M3 must decide how that is shown.
- Deployment hygiene: no automatic dependency-update bot, containers run as root, the database default
  password is still `intake`, stored files are not encrypted at rest, and a shared deployment needs a
  request-size limit on the reverse proxy.

### M3: Extraction

*Goal: invoices are read into structured fields.*

**In plain words.** The system now reads the invoice. For every document it records who the supplier
is, the invoice number and dates, the totals and every line item. Beside each value it keeps *how
sure it is* and *where on the page the value was found*. If a value is not on the invoice, for example
a missing purchase-order number, the system writes "not found". It does not make one up. If it cannot
read a value without guessing (a date like 03/04/2026 could be 3 April or 4 March), it leaves it empty
and marks it as needing a person. Reading costs money, so there is a daily spending limit: when it is
reached, reading pauses (nothing is lost or failed) and the screen says so. Reading the same file twice
never costs twice.

**How it works.**

```
 Prepared pages ──▶ Budget ok? ──▶ Seen before? ──▶ Daily limit ──▶ Ask the model ──▶ Check the answer
 (M2)                too many       yes: reuse the   reached?        (page images     wrong shape? ask once
                     pages: stop    saved answer,    yes: pause      + any text)      more, then mark failed
                                    no charge        until tomorrow
          ──▶ Clean up the values ──▶ Score each field ──▶ Save fields, lines, scores ──▶ status: extracted
              dates, money, currency   how sure are we?
```

1. **What is sent.** The page images, plus the document's own text when it has some. The instructions
   tell the model to copy values exactly as printed, to answer "not found" for anything absent, and to
   ignore any instructions written inside an invoice.
2. **Blank pages are never sent.** A document with nothing on it is marked failed as unreadable, at no cost.
3. **Same file, same answer.** Answers are saved. The same file with the same model and instructions
   is answered from the save, with no new charge and no new call.
4. **Daily limit.** All model spending for the day (UTC) is added up before each call. At the limit,
   extraction jobs wait until the next day, or until the limit is raised. An extra audit entry records the pause.
5. **Wrong shape.** If the answer does not fit the required form, the model is asked once more with a
   description of the problem. If it still does not fit, the invoice is marked failed with a reason.
6. **Cleaning.** Dates become one format, amounts become whole cents, currencies become codes.
   `1.234,56` and `1,234.56` both mean the same amount. Number shapes are checked strictly:
   `1.234.56` or `1,23.45` are refused, not repaired. Anything a person could read two ways is left
   empty, never guessed. A dotted date (`07.06.2026`) is read day-first, the European convention. A bare
   `$` or `¥` is only accepted when the supplier's usual currency says which one it is.
7. **How sure are we (0 to 100%).** Four signals are combined: the model's own confidence, whether the
   value appears in the document's own text, whether the arithmetic supports it (lines add up to the
   subtotal, subtotal plus tax equals the total), and whether the supplier is already known. The model
   saying "high" is **not enough on its own**: a scanned invoice with no text needs supporting evidence
   to reach 80%. Any signal that *contradicts* the value pushes it under 80%, which means a person will look.
8. **Bank details** are stored encrypted and compared by a keyed fingerprint. They are also encrypted in the saved answers.

**Under the hood.**
- **Pure logic** in `core/`, written test-first: `numbers.py` (one strict parser for every printed
  number: validates thousands grouping, refuses ambiguity), `normalize.py` (dates, currency, quantity,
  tax rate, supplier name; ambiguity raises), `money.py` (amounts in whole cents; checks currency codes
  and symbols),
  `confidence.py` (score, signals, text-layer agreement, critical-field check), `extraction.py`
  (turns the raw answer into typed values and per-field results), `llm_budget.py` (price table, cost in
  whole micro-dollars rounded up, cache key, spend cap, UTC-day rollover, per-document budget).
- **Model layer** (`extract/`): `schema.py` (`InvoiceExtraction`; every field is
  `value | null`, `self_confidence`, `page`; the JSON schema is generated from it and uses only what
  strict tools support), `prompts/v1.md` + `prompt.py` (prompts are versioned files, never edited in
  place), `llm.py` (`LlmClient` interface; `AnthropicClient` with a strict `record_invoice` tool,
  forced tool choice except on the two models that reject it, base64 page images, SDK retries),
  `service.py` (cache, cap, one schema retry, `llm_calls` logging in its own transaction),
  `pipeline.py` (the stage), `recorded.py` (replays fixtures keyed by request hash for tests),
  `factory.py` (builds the configured client, or none), `ollama.py` (optional local backend, demo only).
- **Worker**: `extract_invoice` job is queued when `process_document` finishes. A handler can return a
  `Deferral` (pause without using an attempt) for "not configured" and "spend cap reached".
  `queue.stats` counts paused jobs.
- **API**: `GET /extraction/status` (configured, spent today, cap, paused jobs, resume time) and
  `paused` on `GET /jobs`. Migration `0005` adds `llm_calls.response` (the cache) and a spend index.
- **Storage of results**: `invoices` columns, `invoice_lines`, and `field_extractions` (13 header
  fields + 6 per line; `raw_value`, `normalized_value`, `confidence`, `signals`, `page`). Audit events:
  `status_changed` (received, extracting, extracted), `extraction_completed` (model, prompt version,
  cache use, cost, weak critical fields), `extraction_paused`, `extraction_failed`.

**What we proved.** (Recorded answers built from the answer key, not real model output. They exercise
the real PDFs, the real text layers, the real worker and the real database, but not a real model.)

| Check | Result |
|---|---|
| Every clean seed invoice filled `invoices`, `invoice_lines`, `field_extractions` | 59 of 59 (dates in five layouts, EUR, GBP and USD formats, lines, 13 + 6 per line fields). One of them (`inv-092`, a planted currency mismatch) prints a bare `$` but comes from a supplier that normally bills in another currency, so its currency and amounts are correctly left empty for a person |
| A missing PO number comes back empty, not invented | Yes, tested on the 2 clean invoices without one |
| Re-processing the same file uses the saved answer | Yes: model not called, no new `llm_calls` row |
| Hitting the daily cap pauses jobs and shows it | Yes: job waits, `GET /extraction/status` and `GET /jobs` show it, one audit entry, resumes when raised |
| Critical fields trusted (at least 80%) on clean invoices with no planted problems | 35 of 35 |
| Bank account digits found in the database in clear text | None (fields, saved answers, audit, signals) |
| Ambiguous date, ambiguous or misgrouped number, bare `$`, unknown currency, wrong-currency symbol | Left empty with 0% confidence |
| A job that runs out of attempts | The invoice becomes `failed` with a reason (before, it stayed `received` unseen) |
| Bank key rotated, prompt edited in place, NUL characters or invented page numbers in an answer | Old answer ignored and asked again; edited prompt never reuses an old answer; bad answer retried once, never stored |
| Automated tests | 677 pass; decision-logic coverage 99.9%; the manual is checked for every failure reason, empty-value reason, setting and route |

Two independent reviews (decision logic and security) ran. Nothing critical or high was found in
security. The decision-logic review found four ways a wrong value could reach 80% confidence
(misgrouped numbers such as `1,23.45`, a quantity `1.200` read as 1.2, a bare `$` treated as USD, a
currency symbol accepted for the wrong currency). All four were fixed test-first, together with the
daily-limit rollover for non-UTC clocks, the cache key (now includes the prompt text and answer schema),
bounds on model output (no NUL characters, length, page range), a rotated bank key, and invoices whose
job gave up. The remaining review notes are under "Left open".

**Left open.**
- **Accuracy with a real model is not measured.** The evaluation harness arrives in M10. Until then
  the default model (`claude-sonnet-5`) and prompt `v1` are a starting point, not a result. Estimated
  (not measured) cost is about one to three US cents per one-page invoice.
- Model "thinking" effort is left at the default; it may add cost. Tune with the evaluation.
- Free or local models (Ollama) are supported as a demo backend only; their numbers do not apply to the Claude build.
- Fields on scanned and photo documents will often score under 80% (no text to check against). That is intended: they go to a person.
- A permanent provider rejection (bad key) fails the job visibly; fixing it and re-queueing is manual until M8.
- **Text-layer check is not position-aware.** It asks whether a value appears anywhere in the document
  text. A swapped invoice date and due date, or a total equal to a line amount, still agrees. Position-aware
  checks come with the validation rules (M4).
- *(Closed in M4.)* When the currency is left empty (bare `$`), the currency check compares the
  printed symbol with the supplier's usual currency.
- **The daily limit is a check, not a reservation.** With several workers each can overshoot by about one
  call. Failed database writes after a paid call are not counted.
- The API container receives the model key (it only needs to know whether one is set); a later hardening
  step can split the environments. The saved model answers hold supplier data in plain text with no purge
  policy yet (bank account sealed); see `data-handling.md`.
- Still open from M2: sandboxed parsing, deployment hardening. (The M2 item "a failed job leaves the
  invoice in received" is closed: it becomes `failed` with a reason.)

### M4: Validation rules

*Goal: the maths, dates, supplier and bank details are checked.*

**In plain words.** After an invoice is read, the system now checks it the way a careful clerk would.
Do the lines add up to the subtotal, and does subtotal plus tax equal the total? Is the tax what this
supplier normally charges? Is the invoice date sensible (not in the future, not years old), and is the
due date after it? Is the invoice in the supplier's usual currency? Do we know this supplier? Has the
bank account changed? Every check ends in one of three answers: *pass*, *fail* (with the real numbers),
or *could not check*. "Could not check" is never treated as a pass. Nothing is decided yet: the invoice
just carries its results, ready for the explanation and routing step (M7).

**How it works.**

```
 Invoice read (M3) ──▶ Seven checks ──▶ Results saved (pass / fail / could not check) ──▶ status: checking
                        maths, totals, tax, dates,          with the real numbers
                        currency, supplier, bank
```

1. **Line maths.** Quantity times price must equal the line amount (one cent of rounding allowed per line).
2. **Totals.** The lines must add up to the subtotal, and subtotal plus tax must equal the total. The
   total only passes if it was actually compared: matching lines alone never stand in for it.
3. **Tax.** Compared with the supplier's usual rate kept in master data; if unknown, with the rates
   printed on the lines.
4. **Dates.** The invoice date may not be in the future or older than a year (both adjustable), and the
   due date may not be before the invoice date.
5. **Currency.** Must match the supplier's usual currency. If the currency could not be settled on its
   own (a bare `$`), the printed symbol is compared with the supplier's currency instead.
6. **Supplier.** Known only by an exact tax ID, or an exact name or alias. A near-identical name is
   *not* enough: it is shown as "closest match" for a person to judge. If a tax ID and a name point at
   different suppliers, that is a conflict and the supplier is not trusted.
7. **Bank account.** Compared as a one-way fingerprint with the account on file. A different account
   fails; an account that may simply not have been read is "could not check", never a pass.

**Under the hood.**
- **Pure rules** in `core/validate.py` (100% covered, mypy strict, written test-first): `validate()`,
  `match_supplier()`, `ValidationSettings`. Each check is named after the exception it will raise
  (`LINE_MATH_MISMATCH`, `TOTAL_MISMATCH`, `TAX_MISMATCH`, `INVALID_DATE`, `CURRENCY_MISMATCH`,
  `UNKNOWN_SUPPLIER`, `BANK_DETAILS_CHANGED`) and carries a rule version (`v1`).
- **Stage** `checks/pipeline.py::validate_invoice`, chained after extraction as a `validate_invoice`
  job. Results go to `check_results` (unique per invoice, check and version; migration `0007`),
  the known supplier is linked on the invoice, and `checks_completed` is audited (codes and counts
  only, plus the as-of date). A re-run does nothing.
- **Master data:** `suppliers.tax_rate_bp` (migration `0006`, generator and `master.json`, seed loader).
- **Settings** per client in `tenants.settings`: `max_invoice_age_days` (365), `future_date_tolerance_days`
  (0), `line_tolerance_minor` (1), `total_tolerance_minor` (1), `tax_tolerance_per_line_minor` (1),
  `supplier_fuzzy_min` (90). `VALIDATION_TODAY` pins the as-of date for tests and demos and is refused
  when `APP_ENV=production`.

**What we proved.** (Recorded answers built from the answer key; real PDFs, worker and database.)

| Check | Result |
|---|---|
| Planted math, tax, date, currency, supplier and bank problems in the seed data | 21 of 21 caught, across all 118 readable invoices (clean, scanned and photo) |
| Invoices that should clear, and get any failed check | 0 of 60 |
| Failed checks beyond what the seed expects (either planted or an allowed knock-on) | None |
| Look-alike supplier names ("Coastal Packing Ltd." vs "Coastal Packaging Ltd.", 95% similar) | All 3 decoys rejected; the closest match is reported |
| Bank account digits found in results or audit log | None |
| Same invoice checked twice | Nothing added |
| Automated tests | 792 pass |

Two independent reviews (decision logic and security) ran. Security found no critical or high issues.
The decision-logic review found two ways a wrong invoice could pass (a total that "passed" on the lines
alone when tax was missing, and a known name accepted next to a conflicting tax ID); both were fixed
test-first, together with the bank-not-read case, a tolerance that grew with line count, bounds on stored
document text, a tenant check in the stage, an audited and production-guarded as-of date, and a unique
constraint on results.

**Left open.**
- **Deliberate deviation from the playbook:** a fuzzy name match of 90 or more does not make a supplier
  known (ADR 0004). Real supplier names misread by the model will go to a person.
- **M7 must treat `could not check` on currency, bank and supplier, and every failure, as needs-review.**
  The invoice stays in `checking` until then.
- Tax rates come from master data, not from the printed rate. Reading the printed rate would need a
  prompt change and an evaluation (M10).
- Tolerances of one minor unit also apply to zero-decimal currencies (yen); the bank fingerprint is over
  the printed text, so formatting differences give a false "changed" (the safe direction); two suppliers
  sharing an identifier would resolve to the first.
- Check results for an unreadable document (2 seed files) do not exist: they are never extracted.
- A validation job that fails permanently leaves its invoice in `checking` with a failed job (visible in
  `/jobs`); routing (M7) will own that case.

### M5: Duplicate detection

*Goal: likely duplicates are caught before approval.*

**In plain words.** Suppliers sometimes send the same invoice twice, and sometimes retype it slightly
differently (`INV-1043`, then `INV1043`, then `1043`). Paying both is expensive and embarrassing. Each
new invoice is now compared with what the same supplier sent before. If it looks like a repeat, the
result says *which earlier invoice it matches and how*, so a person can put the two side by side. The
earlier invoice is never blamed; only the later one is flagged. If the system cannot tell (a total or
currency could not be read on a similar earlier invoice) it says "could not check" rather than passing.

**How it works.**

```
 Checked invoice ──▶ Earlier ones still being read? ──yes──▶ wait a few seconds (up to 5 minutes)
                              │ no
                              ▼
        same supplier, same number (ignoring case, spaces, dashes)?  ──▶ hard duplicate
        same supplier, same total and currency, dates within 7 days,
        number almost the same (85%+)?                               ──▶ soft duplicate
        otherwise                                                    ──▶ pass (or "could not check")
```

1. **Hard duplicate.** Same supplier and the same invoice number once punctuation, spaces and letter case
   and leading zeros are ignored. `rfm-2026-0504` equals `RFM-2026-0504`, `INV-0043` equals `INV-43`; `TVS20260675` equals `TVS-2026-0675`. Total and
   date do not have to agree: a re-issued invoice with the same number is still suspicious.
2. **Soft duplicate.** Same supplier, exactly the same total and currency, invoice dates within seven days,
   and a very similar number: one digit different, or a bare `1043` against `INV-1043`. A credit note is
   not a duplicate of its invoice (the amounts have opposite signs).
3. **Order.** Only invoices received earlier are compared, so the original is never flagged.
4. **Waiting.** If an earlier invoice is still being read or checked, its details are not final, so the
   check waits (the job shows as paused, nothing fails). After five minutes it goes ahead; a clean result is
   then reported as "could not check", never as a pass.
5. **Could not check.** No supplier, no invoice number, a similar invoice missing its total, date or
   currency (a zero total counts as missing), or a similar number whose supplier is linked on one invoice
   and only printed on the other under a different name.

**Under the hood.**
- **Pure rules** in `core/dedupe.py` (`check_duplicate`, `number_key`, `number_similarity`,
  `supplier_key`, `DedupeSettings`), test-first, 100% covered, mypy strict.
- **Stage** `checks/duplicates.py::detect_duplicates`, chained after validation as a `detect_duplicates`
  job. It records one `POSSIBLE_DUPLICATE` row in `check_results` (unique per invoice and rule version) whose
  `details` name the earlier invoice (id, number, date, kind, similarity, days apart, invoices compared),
  and audits `duplicate_check_completed` (ids and codes only). The job payload's tenant must match.
- **Waiting** is a `Deferral` (`WAITING_FOR_EARLIER_INVOICES`): it uses no attempt and shows under `paused`
  in `GET /jobs`. Settings `DEDUPE_POLL_S` (10) and `DEDUPE_MAX_WAIT_S` (300).
- **Per-client settings** in `tenants.settings`: `dedupe_window_days` (7), `dedupe_number_similarity_min` (85).
- Migration `0008`: index on `invoices (tenant_id, supplier_id)`. ADR 0005 records the decisions. A supplier with more than 2,000 earlier invoices gets `skipped` (`TOO_MANY_TO_COMPARE`), never a truncated comparison.

**What we proved.** (Recorded answers built from the answer key; real PDFs, worker and database; all 118
readable seed invoices.)

| Check | Result |
|---|---|
| Planted duplicates detected, including number-format variants | 5 of 5 (4 hard: exact repeat, lower case, no hyphens; 1 soft: one digit different) |
| Each duplicate names the earlier invoice it matches | Yes, all |
| The original is flagged | Never |
| False duplicates elsewhere in the seed set | None, except one pair the seed labels as PO over-billing (`inv-095` repeats `inv-006` in every field but one digit of the number, so it is a real soft duplicate; see below) |
| Invoices "could not check" | 3 of 118, all beside an earlier invoice from the same supplier whose currency could not be read |
| Waiting for an earlier invoice; wait that runs out; earlier invoice failed | Waits; reports "could not check", never a pass; ignored |
| Same invoice checked twice | Nothing added |
| Invoice numbers in the audit log | None (ids and codes only) |
| Automated tests | 885 pass |

Two independent reviews (decision logic and security) ran. Security found no critical or high issues. The
decision-logic review found one way a real duplicate could pass: a supplier linked on one invoice and only
printed on the other got two different keys, so the two were never compared. Supplier identity is now
compared by record and by printed name, and an irreconcilable pair with close numbers is "could not check".
Also fixed test-first: leading zeros (`INV-0043` vs `INV-43`), blank suppliers, currency case, zero totals as
non-evidence, over-eager "uncertain" between clearly different companies, a bounded candidate set
(refused, never truncated), a uniform tenant filter, a quieter waiting log, and a positive-only wait setting.

**Left open.**
- **Golden label.** The fixed golden set labels the `inv-095` / `inv-006` pair as PO over-billing only. It
  is a genuine soft duplicate and is flagged. The golden set is protected and was not edited; decide in
  M10 whether to widen that label. The M5 acceptance test carries one explicit allowance for it.
- Sequential numbers from one supplier are at least 85% similar, so an invoice with an unreadable total
  or currency beside such an invoice becomes "could not check" (a person looks). Safe, slightly noisy.
- If the five-minute wait expires while an earlier invoice is still unread, a duplicate could hide behind it;
  the result says how many were pending.
- M7 must treat a failed or "could not check" `POSSIBLE_DUPLICATE` as needs-review.
- Received order is the database time the invoice row was created (transaction start). An invoice whose
  transaction started earlier but committed later could be missed by a check that ran in between; ingestion
  commits immediately, so the window is tiny.
- A duplicate result is kept if an invoice is later re-read or corrected; refreshing it belongs with the review
  screen (M8).
- A supplier linked on one invoice and only printed on another is compared by printed name; if the names
  differ and the numbers are close the result is "could not check".
- Digit-only matching also links numbers with different prefixes (`PO-1043` and `INV-1043`) and year-prefixed
  sequences (`2024-0001`, `2025-0001`) when total, currency and date also agree. That errs towards review.

---

### M6: 3-way matching

*Goal: invoices are compared with purchase orders and receipts.*

**In plain words.** A company should only pay for what it ordered, at the price it agreed, and only once
the goods have arrived. Each invoice is now compared with three things: the purchase order (PO), the
delivery receipts, and what other invoices already billed against the same PO. The system finds the PO
(by its number, or, when the invoice names none, by the one open order that fits), pairs each invoice line
with a PO line even when the wording differs a little, and then asks: is the price right, is the quantity
right, has it been delivered, and is the PO's total now exceeded? Every answer names the line and the
numbers, so a person can see exactly what is off. If the system cannot tell (an unreadable price, two
orders that both fit) it says "could not check" rather than passing.

**How it works.**

```
 Checked invoice ──▶ earlier invoices still unread or unmatched? ──yes──▶ wait (up to 5 minutes)
                                   │ no
                                   ▼
   find the PO: by number, else the single open PO of that supplier whose total is close
                                   ▼
   pair lines: SKU ─▶ similar description ─▶ amount   (a line that fits two PO lines is not guessed)
                                   ▼
   price ±2%?  quantity ≤ ordered (counting earlier invoices)?  received ≥ billed?  PO total exceeded?
```

1. **Finding the PO.** By number, ignoring case, spaces and dashes. A number that is not in the system,
   belongs to another supplier, or names a PO that is not open (closed, cancelled) is `PO_NOT_FOUND`. If the
   invoice's supplier is not known, or two of the supplier's POs share the number once punctuation is ignored,
   the result is "could not check". With no number, only the supplier's *open* POs in the same currency are
   considered, and only if the subtotal is within 2% of the PO total; exactly one is used for the other checks
   but reported as "could not check" (`PO_INFERRED`, a person confirms the guess), none is `NO_PO`, and several is
   "could not check" (never a guess).
2. **Pairing lines.** SKU first (a SKU on several PO lines pairs nothing), then description similarity (80 or
   more, so `Blue widgets - box of 10` pairs with `Blue widget, box of 10`), then an identical amount. A PO line
   is used once, and a line whose SKU differs from the PO line's is never paired by description or amount. A
   line that could equally be two PO lines stays unpaired, and an unpaired line is reported as not on the PO.
3. **Price.** Each paired line's unit price must be within 2% of the PO price, above or below.
4. **Quantity.** The billed quantity, plus what earlier invoices already billed on that PO line, may not
   exceed the ordered quantity. Billing less is a partial invoice and is fine.
5. **Receipts.** No receipt at all is `RECEIPT_MISSING`. Otherwise billed quantity (plus earlier billing) may
   not exceed the total received across all receipts: `QTY_NOT_RECEIVED`.
6. **Over-billing.** Earlier subtotals on the PO plus this one may not exceed the PO total (both before tax).
7. **Credit notes.** A negative quantity, amount or subtotal is "could not check" (`CREDIT_NOTE`) and is never
   counted as reducing what has been billed, so a credit cannot make room for a later over-billing.
8. **Order and waiting.** "Earlier" is received order, as in M5. The stage waits for earlier invoices that
   are unread or not matched yet, because they decide what has been billed. After five minutes a clean result
   that depends on earlier billing is reported as "could not check", never as a pass.

**Under the hood.**
- **Pure rules** in `core/match.py` (`check_match`, `match_lines`, `MatchSettings`), test-first, 100% covered,
  mypy strict. Money is integer minor units; quantities are `Decimal`; tolerances are basis points.
- **Stage** `checks/matching.py::match_invoice`, chained after the duplicate check as a `match_invoice` job.
  It writes seven `check_results` rows (one per code, unique per invoice and rule version) and records the
  paired PO line on each `invoice_lines.matched_po_line_id`, which is how later invoices see what was
  billed. It audits `match_completed` (ids, codes and outcomes only). The job payload's tenant must match.
- **What a later invoice reads:** the quantity already matched to each PO line, and each earlier invoice's
  billed amount, stored in its `PO_OVERBILLED` row. If an earlier invoice on the same PO has an unreadable
  quantity or amount, the cumulative checks become "could not check" (`EARLIER_BILLING_UNKNOWN`); this is
  counted per PO, not across the supplier.
- **Waiting** is a `Deferral` (`WAITING_FOR_EARLIER_INVOICES`), like M5. Settings `MATCH_POLL_S` (10) and
  `MATCH_MAX_WAIT_S` (300).
- **Per-client settings** in `tenants.settings`: `match_price_tolerance_bp` (200), `match_qty_tolerance_bp` (0),
  `match_description_similarity_min` (80), `match_po_total_tolerance_bp` (200), `match_overbill_tolerance_minor` (0).
- No migration: the tables (`purchase_orders`, `po_lines`, `goods_receipts`, `receipt_lines`) and
  `invoice_lines.matched_po_line_id` came with M1. ADR 0006 records the decisions. More than 2,000 candidate
  POs gives "could not check" (`TOO_MANY_POS_TO_COMPARE`), never a truncated search.

**What we proved.** (Recorded answers built from the answer key; real PDFs, worker and database; all 118
readable seed invoices.)

| Check | Result |
|---|---|
| Planted PO, price, quantity, receipt and over-billing problems detected | 25 of 25 (4 `NO_PO`, 3 `PO_NOT_FOUND`, 5 price, 4 quantity, 4 not received, 4 no receipt, 1 over-billed) |
| Invoices that should clear and are flagged or left unchecked by any match check | None |
| Other match failures | 4, all knock-ons of a planted line-amount typo that also inflates the printed subtotal above the PO total (`inv-004`, `-064`, `-075`, `-077`); see below |
| Over-billing across invoices | `inv-095` is caught only because `inv-006`'s billing on the same PO is counted |
| Missing receipt vs part-received | Told apart: `RECEIPT_MISSING` (no receipt) vs `QTY_NOT_RECEIVED` (part) |
| Same invoice matched twice | Nothing added |
| Invoice numbers in the audit log | None (ids, codes and outcomes only) |
| Automated tests | 972 pass, decision-logic coverage 99.9% |

Two independent reviews (decision logic and security) ran. Security found no critical or high issues; fixed
test-first or by test: oversized `IN` lists (now subqueries), the earlier-billing sum read row by row in Python
(now summed in the database), an older rule version's billing being counted again, and missing explicit tenant
filters. The decision-logic review found four ways an invoice could pass with a weak match, all fixed test-first:
a named PO that is closed or cancelled, a named PO accepted while the supplier is unknown, two POs sharing a
normalized number, and an inferred PO reported as a clean pass. It also led to credit-note handling, refusing to pair
by a SKU shared by two PO lines, never overriding a conflicting SKU, and treating a NaN quantity as unreadable.

**Left open.**
- **Knock-ons.** The seed's `may_raise` does not list `PO_OVERBILLED` for four invoices whose planted line
  typo also inflates the subtotal beyond the PO total. The flag is correct by the printed numbers; the fixed
  golden set was not edited, and the acceptance test carries one explicit allowance. Decide in M10.
- **Golden label (from M5).** `inv-095` is still labelled over-billing only; unchanged, for M10.
- M7 must treat a failed or "could not check" match result as needs-review, and turn each into an
  exception with its explanation; nothing is raised as an exception yet.
- `CURRENCY_MISMATCH` stays a validation check (M4); matching skips amount checks when currencies differ.
- Only invoices in `failed` or `rejected` status stop counting as billed. When M8 adds corrections or
  rejection, the review screen must refresh or re-run matching for later invoices.
- Description matching uses similarity (`rapidfuzz`), not meaning; two very differently worded lines with
  different SKUs and amounts stay unpaired and go to a person. Safe, slightly noisy.
- Like M5, received order is the database time the invoice row was created.
- PO inference by total is deliberately narrow (open, same supplier, same currency, one candidate), and an
  inferred PO is never a clean pass.
- Line pairing is greedy in invoice order, so an earlier line can take a PO line that a later line fits better;
  the later line then shows as not on the PO. Fail-safe, sometimes a spurious exception.
- The PO number is normalized in the database with an ASCII rule and in Python with a Unicode rule; a PO number
  with accented letters can be missed and reported as not found (toward review). A stored normalized key would fix it.
- A description-only difference is not checked for meaning; `variance_bp` is empty when the PO price is zero, so
  the M7 explanation must handle that.
- M7 must treat every `skipped` match result as needs-review; the unit and acceptance tests only prove that none
  is a pass.

---

### M7: Exceptions and routing

*Goal: every problem has a plain-language explanation and a suggested fix, and every invoice is routed.*

**In plain words.** The earlier steps find problems; this one says what they mean and what to do. Each
problem becomes a card: what is wrong, in words, with the actual numbers ("Line 2 is billed at USD 48.00 per
unit, but the PO says USD 45.00 (+6.7%, limit 2%)"), and what a person should do about it. The system then
decides where the invoice goes. It is *cleared* only when nothing at all is in doubt: no open problem, every
important field read with confidence, the total within the approval limit, and every check done. Anything
uncertain goes to a person. A check that could not be done gets a card too, worded "could not be checked", so
nothing is ever waved through because it was skipped.

**How it works.**

```
 match done ─▶ read every check result ─▶ one card per failed check, one per "could not be checked"
                     │                       + doubtful important fields + total above the limit
                     ▼
     any open problem? doubtful field? total over the limit or negative? a check missing?  ──▶ needs review
     bank account changed (always)                                                          ──▶ needs review
     none of these                                                                          ──▶ cleared
```

1. **One card per check.** A check with several findings lists each line with its numbers in one card.
2. **The words are fixed.** Every explanation is a template filled with the numbers from the check, never free
   text, so it is accurate and consistent. Where the playbook's example did not fit the facts, the template
   was extended (see ADR 0007).
3. **Could not be checked.** A skipped check is raised under its own code at review severity ("The tax could
   not be checked (there is no tax rate on file...), so a person needs to look at it."). One that was skipped
   only because of another problem (an unknown supplier explains its skipped currency and bank checks) is not
   repeated, and a credit note is one card.
4. **Three cards have no check behind them:** a doubtful important field (each named with its percentage), a
   total above the approval limit, and a blank document.
5. **The route.** Cleared (straight through) or needs review, with the reasons recorded: a changed bank account,
   open exceptions, a doubtful field, a missing or negative total, no limit configured, a total above the
   limit, or a missing check.
6. **Blank documents** never reach the checks: they fail at reading, stay `failed`, and carry a block-severity
   `UNREADABLE_DOCUMENT` card.

**Under the hood.**
- **Pure rules** in `core/classify.py` and `core/routing.py`, test-first, both 100% covered, mypy strict.
  `core/exceptions.py` gained variants and the "could not be checked" wording; the taxonomy doc table is
  generated from it and a test keeps them in step.
- **Stage** `checks/routing.py::route_invoice`, chained after the match as a `route_invoice` job. Exceptions,
  route and status commit together; the status change is the idempotency marker. Audit events:
  `exception_raised`, `status_changed` (with the reasons) and `routing_decided`, all codes and ids only.
- **Tenant settings** `approval_amount_limit_minor` (for `approval_limit_currency`, default USD) and
  `approval_amount_limits_minor` (other currencies). A limit is only compared with a total in its own currency
  (no exchange rates); a currency with no limit goes to review. No migration and no new environment settings;
  the demo tenant in `master.json` has limits for USD, EUR and GBP.
- `db/invoices.fail_extraction` raises the `UNREADABLE_DOCUMENT` exception. ADR 0007 records the decisions.

**What we proved.** (Recorded answers built from the answer key; real PDFs, worker and database; all 118
readable seed invoices.)

| Check | Result |
|---|---|
| Every readable invoice is routed | 118 of 118 (34 cleared, 84 needs review) |
| Planted problems raised as exceptions | 58 of 58 |
| An invoice with a planted problem is cleared | Never (false clear rate 0%) |
| Explanations carrying the planted numbers (price, quantity, receipts, PO number, approval limit) | 23 of 23 |
| Explanations with a placeholder, or without a fix | None |
| Cleared invoices with an open exception | None |
| Changed bank account | Block exception; invoice needs review |
| A missing check result | Invoice goes to review |
| Same invoice routed twice | Nothing added |
| Invoice numbers in the audit log | None |
| Automated tests | 1104 pass, decision-logic coverage 99.9% |

Two independent reviews (decision logic and security) ran; neither found a critical or high issue. Fixed
test-first: a skipped check that could suppress itself and leave no exception (latent), a bank check that
could not be done being reported as "bank changed", details that were not understood being worded as
something else (now they fall back to "details not recorded", still raised at the check's own severity), a
confidence that rounded to its own minimum, untrusted document text in explanations (now cleaned and capped),
a zero total (now to review), the approval limit being compared across currencies (now per currency), rule
versions sorted as text, and no lock on the invoice while routing. Not changed: a partial unique index on open
exceptions (needs a migration, left for M8 when exceptions become editable).

**The finding to plan around.** Of the 60 invoices the seed marks as clearable, 34 clear and 26 are held.
Two are held because a similar earlier invoice has no readable currency, so the duplicate check could not be
completed. The other 24 are scans and photos whose *invoice number* scores 75%, below the 80% minimum:
without a text layer nothing confirms it. That is the M3 confidence model and the playbook rule working as
written, not a defect in routing, and no threshold was changed (a change needs `make eval` first). It means
the touchless rate on scanned documents is low until confidence is measured and tuned in M10 (with a real
model the scores may differ).

**Left open.**
- **Confidence on scans and photos** (above): decide after the real-model evaluation.
- **Approval limit values.** The demo limits are one number for USD, EUR and GBP; a real client sets their own
  per currency (there is deliberately no conversion).
- **Correcting a field** must re-run the checks and the routing and reopen exceptions that no longer apply
  (M8). Until then a routed invoice is not re-routed.
- The M8 queue must list `failed` invoices (a blank document) as well as `needs_review`.
- `auto_approve_cleared` is not implemented; approval is a person's action (M8).
- The four extra `PO_OVERBILLED` exceptions (M6 knock-ons) and the `inv-095` golden label are unchanged, for M10.
- `LOW_CONFIDENCE_FIELD` and the duplicate cards appear on invoices the seed calls clearable; the acceptance
  test names exactly which and why.

---

### M8a: The review API

*Goal (first half of M8): a reviewer can act on every seed exception, with the checks re-run on a correction. The browser screen is M8b.*

**In plain words.** Until now the system found problems and explained them, but nobody could do anything about
them. This step adds everything a reviewer needs, as an API that the screen will sit on. A reviewer signs in,
sees the queue (worst problem first), opens an invoice, and can read every field with how sure the system is and
*why* it is unsure, see the exceptions with their explanations, and act: correct a field, resolve or dismiss an
exception, approve, reject, or note that information was requested. Correcting a field is the important one: the
system immediately runs every check again with the new value and shows the new result, closing problems that
went away. Approval is only possible once every exception is closed, and the riskiest ones (a changed bank
account, a duplicate, an over-billed order) need a written note. Everything a person does is recorded under
their name.

**How it works.**

```
 sign in ─▶ queue ─▶ open invoice ─▶ correct a field ─▶ checks + routing run again ─▶ updated invoice
                                 └─▶ close exceptions (block needs a note) ─▶ approve / reject
```

1. **Signing in.** One demo reviewer. The password is in `.env`; the API returns a signed session that expires
   (8 hours). Five wrong passwords in a minute lock the login for that minute. With no password configured
   nobody can log in.
2. **The queue.** Invoices needing a person (`needs_review`, and `failed` ones, which include a blank document
   with its `UNREADABLE_DOCUMENT` card), worst open exception first and then oldest; filter by exception, supplier
   and status.
3. **One invoice.** Fields with confidence and a plain-words reason when doubtful (for example "nothing
   confirms it: no text layer, rule or supplier record supports it"), the lines, exceptions with explanation and
   fix, the checks, why it was routed, and whether it can be approved. A duplicate exception carries the other
   invoice for a side-by-side view. Bank accounts are masked, and pages are served as images.
4. **Correcting a field.** Header fields only. The value is read with the same parsers that read the document
   (so `1,234.50` means the same thing), stored beside the original, and the checks and routing run again in the
   same moment. Exceptions that no longer apply are closed by the system; one a person already closed, with the
   same words and numbers, stays closed.
5. **Deciding.** Approve (only when every exception is closed, from `cleared` or `needs_review`), reject (a
   reason is required), request information (logged, the status does not change), reveal a bank account (logged).
   Approve and reject are final.

**Under the hood.**
- **Pure rules** in `core/review.py` (approval, notes, corrections, plain-words confidence reasons, carry-over of
  a reviewer's decision) and `core/session.py` (signed sessions, login throttle), test-first, both 100% covered,
  mypy strict.
- **Service** `review/service.py`: each action is one transaction with a `review_actions` row and audit events
  (field names and codes, never values). A correction sets the corrected value beside the original, clears the
  old results and matches, and runs validation, duplicates, the match and routing again, never waiting for other
  invoices (a person is at the screen).
- **API** `api/login.py` and `api/invoices.py`; `require_user` guards actions. The static token can read but not
  act. The upload guard accepts either kind of token.
- **Status flow.** Two edges added to the playbook diagram: an open invoice can return to `extracted` for a
  re-check, and a cleared invoice can be rejected. No migration.
- ADR 0008 records the decisions.

Two independent reviews (decision logic and security) ran; neither found a critical or high security issue.
Fixed test-first: a reviewer's dismissal of a changed bank account being carried over (its words name no account,
so it is now raised again after every re-check and always needs a fresh decision), a currency change that would
have left amounts in the wrong scale, amounts too large for the database, control and invisible characters in
corrections and notes (an invisible or punctuation-only note no longer counts as the note a block needs), a login
throttle that a burst of parallel guesses could beat and that locked the real reviewer out for everyone (now
atomic, per client and overall, with `Retry-After` and audited sign-ins), sessions that outlived the login being
switched off, a stale read of an exception after waiting for the invoice lock (two reviewers could both close it),
an unreadable bank account causing a crash, and page images and reveals being cached by the browser.

**What we proved.** (Recorded answers built from the answer key; real PDFs, worker, database and HTTP; all 118
readable seed invoices.)

| Check | Result |
|---|---|
| Correcting the total that does not add up removes that exception and re-routes | Yes: one fresh result, the old exception closed by the system, the checks run again |
| A reviewer's dismissal survives a re-check of an unrelated field | Yes |
| Approval while an exception is open | Refused (409 `OPEN_EXCEPTIONS`) |
| Closing a block exception without a note | Refused (422 `NOTE_REQUIRED`) |
| Approved or rejected invoice corrected, approved or rejected again | Refused (409 `WRONG_STATUS`) |
| Unreadable or unknown corrections (`abc` as a total, the bank account, a line field) | Refused, nothing changed |
| Action with the static API token | Refused (403) |
| Another tenant's invoice or exception | Not found, for every action |
| Bank account in any response or audit event | Never in full; a reveal is logged without it |
| Queue order | Worst open exception first, then oldest; filters and paging work |
| Automated tests | 1320 pass, decision-logic coverage 99.9% |

**Left open.**
- **The screen** (M8b): queue, invoice page with the document, cards and actions, 375 px layout, and the
  Playwright smoke test (upload, review, approve) with recorded model answers in CI.
- **Line corrections.** Only header fields can be corrected; fixing a line quantity or price (for a
  `LINE_MATH_MISMATCH`) is not yet possible.
- **Later invoices are not re-matched.** Correcting an invoice changes what it bills against its PO, but invoices
  already matched after it keep their old result until they are corrected (runbook).
- **Request information** is a log entry; there is no "waiting" status and nothing is sent to the supplier.
- The demo login is one shared identity; a real user system is needed before real data. Sessions cannot be
  revoked one by one (only by changing `SESSION_SECRET`, the reviewer name or emptying the password).
- `auto_approve_cleared` is still not implemented; a person approves every invoice.
- No partial unique index on open exceptions per (invoice, code); the row lock on an invoice in the review
  service and the routing stage covers concurrent actions.
- A currency can only be corrected while no amounts were read; a wrong currency on an invoice with amounts is
  rejected and re-requested, because amounts cannot be re-read from here.
- `review_actions` holds invoice values (before and after a correction) and free-text notes; its retention and
  access are recorded in `data-handling.md`.

---

### M8b-1: The review screens

*Goal (second half of M8, first part): a reviewer opens a browser, signs in, and sees the queue and every invoice with its document. Acting from the browser is M8b-2.*

**In plain words.** The review API now has a face. A reviewer signs in and lands on the queue: the invoices that
need a person, worst problem first, each with a red or amber stamp, the supplier, the total, and the problem in
plain words. Opening one shows the document next to the findings: every problem explained with its real
numbers and the suggested fix; the fields with how sure the system is and *why* it is unsure; the lines; every
check; and, for a possible duplicate, the two invoices side by side with what differs marked. It works on a
phone, in light or dark, and the document is one tap away. A page for uploading a new invoice is there too.
This release is read-only on purpose: deciding from the browser (correct, close, approve, reject) follows in
M8b-2, so it can be reviewed and tested as its own piece.

**How it works.**

```
 browser ─▶ web server (Next.js) ─▶ API
   cookie only       holds the session, calls the API as the reviewer
   (no token)
```

1. **Signing in.** The sign-in page posts to the web server, which asks the API to log the reviewer in and
   keeps the returned session in a cookie that scripts cannot read and other sites never receive. The API token
   never reaches the browser. Every page but sign-in redirects there when there is no session; a stale session
   is caught by the API and lands on sign-in again.
2. **The queue.** Three tabs (needs review, unreadable, cleared) with counts; a filter by exception; a link to
   narrow to one supplier; paging. Filters live in the address, so a queue can be bookmarked. Anything invalid in
   the address is ignored, never passed on.
3. **The invoice.** Document viewer (pages, zoom, arrow keys, jump-to-page from a field), then tabs for
   exceptions, fields, lines and checks. The five *key fields* (supplier, invoice number, date, total,
   currency) are shown first, because they are what decides where an invoice goes; the others follow.
4. **Upload.** A drop zone; the file goes through the web server to the same API upload as before.
5. **Look and feel.** A ledger: warm paper, ink, and the vermilion of a rubber stamp; a serif for names and
   headings, a clean sans for reading, a mono for amounts; severity shown as pressed stamps; light and dark.

**Under the hood.**
- **Next.js 16 (App Router).** Pages are server components that call the API through one server-only module
  (`lib/api/server.ts`); Server Actions handle sign-in, sign-out and upload (they carry Next's own origin
  check). Interactive parts (document viewer, tabs, menus) are small client components that receive plain data.
- **Proxy** (`proxy.ts`): a per-request nonce for a strict Content Security Policy (`script-src` by nonce,
  `frame-ancestors 'none'`, `form-action 'self'`, own-site images and fonts only), security headers, and a quick
  redirect to sign-in when there is no cookie (the API is what really decides). Page images are fetched through a
  route that adds the session, so the browser never talks to the API directly.
- **Components**: shadcn/ui patterns on Radix primitives with Tailwind 4; icons from lucide; fonts self-hosted
  through npm (Newsreader, Instrument Sans, IBM Plex Mono), so a build needs no network and no font CDN.
  Radix's scroll lock needs the nonce, which is handed to it once; the toast library was dropped because it
  cannot take one.
- **Types** come from `openapi.json`, a committed snapshot of the API schema (`make gen-api`, no server needed).
  A test fails if the API changes without regenerating. To make them precise the API's invoice header became a
  typed model and its two `DocumentOut` models were renamed.
- **Pure helpers** with tests: money, dates and ages, exception and status labels, queue filters, safe redirect,
  CSP, cookie options, public paths.
- ADR 0009 records the decisions. No migration; no new API behaviour beyond the typed header.

**What we proved.** (Real API, worker and database with the seed invoices run through the pipeline on recorded
answers; a real browser.)

| Check | Result |
|---|---|
| Sign in, queue, invoice (with document), upload on real data | Work end to end, in light and dark |
| Browser console under the strict CSP | Clean (two library style injections were found and removed or given the nonce) |
| Horizontal scroll at 375 px on the queue and the invoice | None (a header overflow was found and fixed) |
| Page image, zoom, jump to a field's page, document sheet on a phone | Work |
| An invoice number or PO shown as printed (not the lower-cased matching form) | Yes |
| Upload through the browser | Stored, with a link to the invoice |
| Dependencies | `pnpm audit` clean; every licence permissive (MIT, ISC, Apache-2.0, OFL for the fonts) |
| Automated tests | 1322 API tests and 40 web tests pass; lint, types and a production build are clean |

**Left open.**
- **M8b-2**: correcting a field, closing exceptions, approve, reject, request information and the bank reveal
  from the browser; the browser test (upload, review, approve, and no horizontal scroll at 375 px) with a recorded
  model provider, running in CI. Until then the screens are read-only.
- The queue counts use one small request per tab; a single summary endpoint would be cheaper for a large queue.
- The document viewer shows pages and jumps to a field's page, but cannot highlight where a field sits: the
  reader records a page, not a position.
- No automated accessibility audit yet (axe); keyboard, contrast and labels were checked by hand.
- Component-level tests are not written; the pure logic is tested, and the browser test in M8b-2 covers the flow.

---

## 4. Roadmap: what each remaining milestone will add

| # | In plain words | You will be able to |
|---|---|---|
| **M8b-2 Reviewer actions** | Correct a field, close exceptions, approve, reject and ask for information from the browser, with a browser test (upload, review, approve) that runs in CI. | Clear the review queue |
| **M9 Audit timeline** | A readable history of everything that happened to an invoice. | Answer "what happened to this invoice?" |
| **M10 Accuracy report** | Measured accuracy per field and per document quality on the fixed golden set. | Publish honest numbers; false clear rate 0% |
| **M11 Dashboard** | Invoices processed, touchless rate, exceptions by reason, cost per invoice, estimated savings (assumptions labelled). | Show business value |
| **M12 Export** | Approved invoices leave as CSV or JSON, through a connector design that lets accounting systems be added later. Only approved invoices can be exported. | Hand data to accounting |
| **M13 Demo polish** | Rehearsed 2 to 3 minute demo, one-command reset, recording. | Show it to clients |

Model calls (built in M3) are the only part that costs money. Rule: changing a prompt, model, threshold
or rule requires running the evaluation and reporting the result first.

---

## 5. Quality evidence over time

| After | API tests | Decision-logic coverage | CI | Notable proof |
|---|---|---|---|---|
| M0 | 1 (plus 2 web) | n/a | Green | Stack starts; "API: healthy" |
| M1 | 119 | 100% | Green | All 18 exception codes planted; audit log immutable |
| M2 | 277 | 100% | Green on #1 and #2; #3 pending | 120/120 quality classification; hostile-upload guard |
| M3 | 677 | 99.9% | Green | 59/59 clean invoices extracted end to end; cache and spend cap proved; no bank digits in clear |
| M4 | 792 | 99.9% | Green | 21/21 planted problems caught, 0/60 clearable invoices flagged; look-alike suppliers rejected |
| M5 | 885 | 99.9% | Green | 5/5 planted duplicates found (incl. lower-case and no-hyphen numbers); originals never flagged |
| M6 | 972 | 99.9% | Green | 25/25 planted PO problems found; over-billing counted across invoices; no clearable invoice flagged |
| M7 | 1104 | 99.9% | Green | 58/58 planted problems raised as exceptions; 0 false clears; every explanation carries its real numbers (23/23 checked) |
| M8a | 1320 | 99.9% | Green | Correcting a field re-runs the checks and routing; approval blocked while any exception is open; every action audited without values |
| M8b-1 | 1322 (+ 40 web) | 99.9% | Pending | Sign-in, queue and invoice screens on real data; no horizontal scroll at 375 px; clean browser console under a strict CSP |

The build gates on decision-logic coverage of at least 90%.

---

## 6. Decisions

| Decision | Why | Where |
|---|---|---|
| Pin TypeScript 5.9 and ESLint 9 | Newer majors broke the API type generator and the linter | ADR 0001 |
| Compose host ports configurable | Developer machines often have 8000, 5432 or 3000 taken | ADR 0002 |
| Bank accounts encrypted, compared by keyed hash | Never need the plain number to compare | M1, `security.py` |
| Golden set fixed and never overwritten | Keeps accuracy results comparable over time | M1, guard hook |
| Interim bearer-token login on every route except health | Playbook forbids public endpoints; real login arrives in M8 | M2 |
| Reject corrupt or password-protected files at upload | Illegible-but-openable files go on and raise a normal exception | M2 |
| Job errors record only the exception class | Messages could echo document content | M2 |
| Database published on localhost only | Removes network exposure of the audit log and bank data | M2 |
| Audit and rendering done in the smallest safe steps | Hostile files cannot exhaust memory | M2 |
| Model output is untrusted: strict schema, then Python validation, then normalization | Nothing the model says reaches the database unchecked | M3 |
| Uncertain means empty plus zero confidence (ambiguous dates, junk amounts, unknown currencies) | A guess that looks right is worse than a blank a person fills | M3 |
| Confidence never rests on the model's word alone; any contradicting signal caps it under 80% | Protects the false-clear rate | M3 |
| Answers cached by (file, model, prompt version); every call logged in its own transaction | A paid answer is never lost or paid for twice | M3 |
| Daily spend cap in UTC, pauses jobs instead of failing them | Nothing is lost; visible in the API | M3 |
| Default model `claude-sonnet-5` ($2 in / $10 out per million tokens), price table in code | Mid-tier start per the playbook; unknown models are refused, not guessed | M3 |
| Dotted dates (07.06.2026) are day-first; slash and hyphen dates are ambiguous | European convention for dots; the rest needs a hint or a person | M3 |
| Bank account encrypted in the saved answers and the field table | Playbook section 10 | M3 |
| A job that runs out of attempts marks its invoice `failed` with a reason (`JOB_FAILED:<Class>`) | An invoice must never sit unseen in `received` | M3 |
| Bare `$` and `¥` are settled only by the supplier's usual currency | `$` is also CAD and AUD; a guess would pass a wrong currency | M3 |
| A supplier is known only by exact identifiers that agree; a fuzzy name only suggests | Look-alike names are an impersonation pattern; deviates from playbook 6.4 | M4, ADR 0004 |
| "Could not check" is its own outcome and never a pass | Uncertain means human | M4 |
| A total only passes when subtotal plus tax was compared with it | Matching lines alone must not clear a wrong total | M4 |
| Supplier tax rate kept in master data | Tax can be checked without a prompt change | M4 |
| As-of date override is refused in production and audited | It could silently disable the date checks | M4 |
| Only the later invoice is flagged; the check waits for earlier invoices to be read | The original must not be blamed, and a copy must not be checked before its original is readable | M5, ADR 0005 |
| Sequential numbers rely on matching total, date and currency; missing data is "could not check" | A near number alone is not evidence | M5 |
| Line pairing is SKU, then similar description, then amount; a line that fits two PO lines is not guessed | A wrong pairing would compare the wrong prices and could clear a bad invoice | M6, ADR 0006 |
| Billing more than ordered is a variance; billing less is a partial invoice | Invoices are often split across deliveries | M6, ADR 0006 |
| Over-billing is counted across invoices in received order, and the check waits for earlier ones | The total on one invoice cannot show an over-billed PO | M6, ADR 0006 |
| A skipped check is raised under its own code as "could not be checked", at review severity | A check that could not be done must never clear an invoice, and the taxonomy stays fixed | M7, ADR 0007 |
| One exception per failed check, listing every affected line | Keeps the queue short; matches playbook 6.7 | M7, ADR 0007 |
| Routing clears only if nothing is in doubt: exceptions, confidence, limit, and every check present | Uncertain means human; false clear rate must stay 0% | M7, ADR 0007 |
| The approval limit is compared in the invoice's own currency | There is no exchange-rate data; a limit not set means review | M7, ADR 0007 |
| A blank document stays `failed` and carries an `UNREADABLE_DOCUMENT` exception | Playbook 5.2 allows only a retry from `failed` | M7, ADR 0007 |
| The web server holds the session in an httpOnly, SameSite=Strict cookie and calls the API itself; the API token never reaches the browser | A script in the page (or a browser extension) cannot read the session | M8b-1, ADR 0009 |
| A strict Content Security Policy with a per-request nonce; a library that cannot take a nonce (toasts) was dropped rather than the policy loosened | An injected script or style must not run | M8b-1, ADR 0009 |
| shadcn/ui on Radix, hand-styled with Tailwind, with self-hosted fonts and an editorial "ledger" look | Accessible dialogs and menus, a polished look for clients, no font CDN | M8b-1, ADR 0009 |
| The web app's API types come from a committed schema snapshot, and a test fails if the API changes without regenerating | The screen must never talk to an API it has stale types for | M8b-1, ADR 0009 |
| One demo reviewer signs in with a password; the API issues a short-lived signed session (HMAC, 8 hours) | A real user system is out of scope for the demo; the history still names who acted | M8a, ADR 0008 |
| The static API token can read the queue but not act; every action needs a reviewer session | Every decision must name a person | M8a, ADR 0008 |
| Approval needs every exception resolved or dismissed; a block exception needs a written note | No approval "over" an unread warning | M8a, ADR 0008 |
| A correction re-runs the checks and routing at once; the reviewer's earlier decisions on identical exceptions are kept | A person should not have to dismiss the same thing twice | M8a, ADR 0008 |
| An open invoice can go back to `extracted` (re-check) and a cleared one can be rejected | The playbook diagram had no way to act on a correction | M8a, ADR 0008 |
| Bank accounts are shown masked; revealing one is a logged action | Playbook section 10 | M8a |
| Local (Ollama) backend is optional and demo-only | No data leaves the machine, but accuracy is lower and does not transfer | M3 |

## 7. Risks and open questions

| Item | Impact | Plan |
|---|---|---|
| No sandboxed parsing process with a time limit | A crafted file could still slow the worker | Before any real-data use |
| Permanently failed job leaves invoice as "received" | Reviewer may not see it | Handled in M3: it becomes `failed` with a reason (see the M3 chapter) |
| Default database password, root containers, unpinned base image | Weak for shared deployments | Harden before hosting |
| Almost every scanned or photographed invoice is routed to review (invoice number scores 75%, below the 80% minimum) | Low touchless rate on scans; the real-model score may differ | Measure with `make eval` (M10) before touching any threshold |
| Real-model accuracy and cost unmeasured; model choice is provisional | Numbers could be worse than hoped | Run `make eval` (M10) with approval before quoting any figure |
| The login route is public (it has to be) and the demo has one shared reviewer identity | A weak password or a leaked session gives a reviewer's powers | Throttled (5 wrong tries a minute), passwords generated, sessions expire; a real user system before any real data |
| Hosting, login method, target currencies and languages, licensed real samples | Open decisions in the playbook (section 15) | Settle before M8 and M10 |
| Fresh-clone run on another machine not yet done | M0 acceptance criterion | Ask a teammate to follow the manual |

## 8. How this report is kept up to date

Every milestone pull request must:
1. Add or update its chapter here, with all three layers.
2. Move the row in the status board and the roadmap table.
3. Add the new evidence to the quality table (test counts, measured results, what was proved).
4. Record new decisions and any items left open.
5. Update [`manual.md`](manual.md) for anything a user or operator can now do.

A pull request that changes behaviour without updating both documents is not complete.
