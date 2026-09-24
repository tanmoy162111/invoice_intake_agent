# Invoice Intake Agent: Project Report

> **Living document.** Every milestone adds its own chapter in the same pull request as the code.
> **Last updated:** after M2 (ingestion and job queue), 2026-09-25.
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
- **Where we are.** Three of fourteen milestones are built and open for review (pull requests #1 to #3).
  A file can be uploaded, safely stored, deduplicated, turned into page images, and classified. A
  realistic demo world of 120 invoices exists to test against.
- **What is not built yet.** Reading the fields (M3), the checks (M4 to M7), the reviewer screen (M8),
  audit timeline (M9), accuracy report (M10), dashboard (M11), export (M12), demo polish (M13).
- **Health.** 277 automated tests pass. The decision-logic code has 100% test coverage. Automated
  checks (CI) pass on the first two pull requests.

### Status board

| # | Milestone | What it gives you | Status |
|---|---|---|---|
| M0 | Project skeleton | One command starts everything | Built, in review (PR #1) |
| M1 | Data model and synthetic data | A database and a messy, realistic demo world | Built, in review (PR #2) |
| M2 | Ingestion and job queue | Upload a file; it is stored and prepared | Built, in review (PR #3) |
| M3 | Extraction | Fields read from the invoice, with confidence | Planned |
| M4 | Validation rules | Math, dates, supplier and bank details checked | Planned |
| M5 | Duplicate detection | Repeats caught before approval | Planned |
| M6 | 3-way matching | Invoice compared with PO and receipt | Planned |
| M7 | Exceptions and routing | Plain-language explanation and next step for every problem | Planned |
| M8 | Review queue UI | A reviewer clears the queue in a browser | Planned |
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

---

## 4. Roadmap: what each remaining milestone will add

| # | In plain words | You will be able to |
|---|---|---|
| **M3 Extraction** | The system reads the invoice: supplier, numbers, dates, lines, totals, each with a confidence level and where on the page it was found. If a field is missing it says "not found" instead of guessing. Model calls are capped by budget and cached so nothing is paid for twice. | See structured data for every clean seed invoice |
| **M4 Validation** | Does the math add up? Are dates sensible? Is the supplier known? Did the bank account change? | See which checks passed or failed and why |
| **M5 Duplicates** | Same invoice sent twice, even with the number typed differently. | Catch repeats before approval |
| **M6 Matching** | Compare against the purchase order and delivery receipt; price, quantity, missing delivery, over-billing. | See the three-way comparison |
| **M7 Explanations and routing** | Every problem gets a plain-language explanation with the real numbers and a suggested fix; each invoice is routed to "cleared" or "needs a person". | Read exactly what is wrong and what to do |
| **M8 Reviewer screen** | A browser queue: document on one side, fields on the other, actions to correct, approve, reject. Bank details masked. Works on a phone. | Clear the review queue |
| **M9 Audit timeline** | A readable history of everything that happened to an invoice. | Answer "what happened to this invoice?" |
| **M10 Accuracy report** | Measured accuracy per field and per document quality on the fixed golden set. | Publish honest numbers; false clear rate 0% |
| **M11 Dashboard** | Invoices processed, touchless rate, exceptions by reason, cost per invoice, estimated savings (assumptions labelled). | Show business value |
| **M12 Export** | Approved invoices leave as CSV or JSON, through a connector design that lets accounting systems be added later. Only approved invoices can be exported. | Hand data to accounting |
| **M13 Demo polish** | Rehearsed 2 to 3 minute demo, one-command reset, recording. | Show it to clients |

Model calls (M3 onward) are the only part that costs money. Rule: changing a prompt, model, threshold
or rule requires running the evaluation and reporting the result first.

---

## 5. Quality evidence over time

| After | API tests | Decision-logic coverage | CI | Notable proof |
|---|---|---|---|---|
| M0 | 1 (plus 2 web) | n/a | Green | Stack starts; "API: healthy" |
| M1 | 119 | 100% | Green | All 18 exception codes planted; audit log immutable |
| M2 | 277 | 100% | Green on #1 and #2; #3 pending | 120/120 quality classification; hostile-upload guard |

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

## 7. Risks and open questions

| Item | Impact | Plan |
|---|---|---|
| No sandboxed parsing process with a time limit | A crafted file could still slow the worker | Before any real-data use |
| Permanently failed job leaves invoice as "received" | Reviewer may not see it | Decide in M3 |
| Default database password, root containers, unpinned base image | Weak for shared deployments | Harden before hosting |
| First model choice, hosting, login method, target currencies and languages, licensed real samples | Open decisions in the playbook (section 15) | Settle before M3, M8 and M10 |
| Fresh-clone run on another machine not yet done | M0 acceptance criterion | Ask a teammate to follow the manual |

## 8. How this report is kept up to date

Every milestone pull request must:
1. Add or update its chapter here, with all three layers.
2. Move the row in the status board and the roadmap table.
3. Add the new evidence to the quality table (test counts, measured results, what was proved).
4. Record new decisions and any items left open.
5. Update [`manual.md`](manual.md) for anything a user or operator can now do.

A pull request that changes behaviour without updating both documents is not complete.
