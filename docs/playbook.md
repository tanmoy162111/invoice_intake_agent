# Demo A: Invoice Intake Agent — Developer Playbook

> **Audience:** the BeyondAI team and Claude Code.
> **Purpose:** everything needed to build Demo A, in order, with clear "done" points.
> **Status:** v1, September 2026. Update this file in the same PR as any change to scope, architecture, or conventions.

---

## 0. How to use this playbook

- **Read sections 1–4 once** before writing any code. They explain what we're building and why.
- **Build with section 9 (Milestones).** Each milestone is written so it can be copied into a GitHub issue as-is.
- **Claude Code:** work on **one issue at a time**, on its own branch, and follow the rules in section 11. Do not start a later milestone until the current one meets its acceptance criteria.
- If anything here is unclear or seems wrong, **stop and ask** in the issue. Don't guess. Asking is faster.

---

## 1. What we're building

An **invoice intake agent** that:

1. Receives supplier invoices (PDF, scans, photos).
2. Reads the key fields and line items, with a confidence level for each field.
3. Checks them: the math adds up, the supplier is known, the dates make sense.
4. Detects likely duplicates.
5. Matches the invoice against the purchase order (PO) and goods receipt.
6. **Explains every exception in plain language and suggests a fix.** This is the core of the product.
7. Sends anything uncertain to a person in a review queue, where they approve, correct, or reject it.
8. Logs every action, so each invoice has a complete, auditable history.
9. Exports approved invoices in a format an accounting system can import.

### Why the product is shaped this way

Our research found that **reading invoices is a commodity; exceptions are where AP teams actually lose time.** Most exceptions come from PO mismatches, missing receipts, duplicates, and messy supplier data, not from misread text. So Demo A must be excellent at **explaining and routing exceptions**, not just at extraction.

### The pitch this demo supports

> "Your team isn't slow at typing. It's stuck on exceptions."

### Hard rules (never break these)

1. **The agent never pays anyone and never moves money.** It prepares, checks, explains, and routes. People approve.
2. **Every action is logged**: who or what did it, when, and why.
3. **Uncertain means human.** If a check fails or confidence is low, the invoice goes to the review queue.
4. **No real client data in the repo, ever.** Only synthetic or properly licensed sample data.
5. **Accuracy is reported per field and per document type**, never as one headline number.

---

## 2. Scope

### In scope for Demo A

- Upload invoices through the web UI and the API.
- Extraction of header fields and line items.
- Validation rules, duplicate detection, and 3-way matching (invoice ↔ PO ↔ receipt).
- An exception taxonomy with plain-language explanations and suggested fixes.
- A review queue, an invoice detail screen, and an audit timeline.
- A metrics dashboard showing touchless rate, exceptions by reason, and cost per invoice.
- An evaluation harness with a golden dataset and a written accuracy report.
- CSV/JSON export, plus a **connector interface** that real accounting systems can plug into later.
- A synthetic data generator that produces realistic suppliers, POs, receipts, and messy invoices.

### Out of scope for Demo A (but designed for, see section 12)

- Real accounting or ERP connections (QuickBooks, Xero, NetSuite, etc.).
- Email inbox ingestion. The ingestion interface supports it; the adapter comes later.
- Multi-client production hosting, SSO, and role-based permissions beyond a simple "reviewer" role.
- Payments of any kind. **Permanently out of scope for the agent.**

### Definition of done for Demo A

- [ ] `docker compose up` starts the full system locally with seeded demo data.
- [ ] The full pipeline runs end to end on the demo dataset without manual fixes.
- [ ] The evaluation report exists, with per-field accuracy split by clean, scanned, and photo invoices.
- [ ] Every planted exception in the demo dataset is detected and explained.
- [ ] The review queue, detail screen, audit timeline, and dashboard all work on desktop and are usable on a phone.
- [ ] A 2–3 minute walkthrough video is recorded (section 13).
- [ ] The docs are complete: README, architecture, data handling, and runbook.

---

## 3. Architecture

### Overview

```
                 ┌──────────────────────────────────────────────┐
  Upload (web/API)│                 FastAPI (api)                │
 ───────────────▶ │  /documents  /invoices  /review  /metrics    │
                 │  /export  /audit                              │
                 └───────────────┬──────────────────────────────┘
                                 │ enqueue job
                                 ▼
                 ┌──────────────────────────────────────────────┐
                 │            Worker (same codebase)             │
                 │                                              │
                 │  1 Ingest ─▶ 2 Extract ─▶ 3 Normalize        │
                 │      ─▶ 4 Validate ─▶ 5 Dedupe ─▶ 6 Match     │
                 │      ─▶ 7 Classify exceptions ─▶ 8 Route      │
                 └───────────────┬──────────────────────────────┘
                                 │
                 ┌───────────────▼──────────────┐   ┌──────────────┐
                 │          PostgreSQL           │   │ File storage │
                 │ invoices, checks, exceptions, │   │ originals +  │
                 │ audit_events, llm_calls, ...  │   │ page images  │
                 └───────────────▲──────────────┘   └──────────────┘
                                 │
                 ┌───────────────┴──────────────┐
                 │        Next.js (web)          │
                 │ queue · detail · audit ·      │
                 │ dashboard · upload            │
                 └──────────────────────────────┘
```

### Key design decisions

| Decision | Choice | Why |
|---|---|---|
| Backend language | Python | Best libraries for PDF, image, and document work |
| API framework | FastAPI + Pydantic v2 | Typed schemas, automatic OpenAPI docs, which the frontend types are generated from |
| Database | PostgreSQL | Relational data (PO ↔ invoice ↔ receipt), JSONB for raw outputs, reliable job queue |
| ORM / migrations | SQLAlchemy 2 + Alembic | Standard, well understood by Claude Code |
| Background jobs | A small Postgres-backed job table (`SELECT … FOR UPDATE SKIP LOCKED`) | No extra infrastructure. Can be swapped for a queue later |
| Extraction | Claude API with a strict JSON schema (tool use) | Handles any layout without templates. The model is set in config |
| PDF handling | `pypdfium2` (render pages), `pdfplumber` (text layer) | No system dependencies like poppler |
| Fuzzy matching | `rapidfuzz` | Supplier names and duplicate detection |
| Money | Integer minor units (cents) + currency code | Never use floats for money |
| Frontend | Next.js (App Router) + TypeScript + Tailwind | Fast to build, easy to deploy a preview |
| API types in the web app | `openapi-typescript`, generated from FastAPI's schema | One source of truth |
| Python tooling | `uv`, `ruff`, `pytest`, `mypy` (strict on `core/`) | Fast and consistent |
| JS tooling | `pnpm`, `eslint`, `prettier`, `vitest`, `playwright` (smoke tests) | Standard |
| Local run | Docker Compose: `db`, `api`, `worker`, `web` | One command to start |

Record every significant decision as a short ADR in `docs/decisions/` (template in section 14).

### Why Claude for extraction (and how to keep it honest)

- **Why a vision-language model:** it reads any supplier layout without per-supplier templates. That's the main advantage over classic OCR.
- **Model choice is configuration, not code:** set `EXTRACTION_MODEL` in `.env`. Start with a mid-tier model and measure it with the eval harness (section 8) before changing anything.
- **Always force structured output**, using a tool/function with a JSON schema that matches `InvoiceExtraction` (section 5.2).
- **Never trust the model's confidence on its own.** Field confidence combines several signals (section 6.3).
- Check the current Anthropic docs (https://docs.claude.com) for the exact SDK calls, model names, and PDF or image input options before implementing. Do not rely on memory for API details.

---

## 4. Repository structure

```
beyondai-intake/
├── CLAUDE.md                     # Rules for Claude Code (section 11.3)
├── README.md                     # What it is, how to run it, how to demo it
├── docker-compose.yml
├── .env.example                  # Every variable documented; no secrets
├── apps/
│   ├── api/
│   │   ├── pyproject.toml
│   │   ├── alembic/              # Migrations
│   │   ├── src/intake/
│   │   │   ├── main.py           # FastAPI app factory
│   │   │   ├── config.py         # Settings (pydantic-settings)
│   │   │   ├── db/               # Models, session, repositories
│   │   │   ├── api/              # Routers: documents, invoices, review, metrics, export, audit
│   │   │   ├── worker/           # Job table, runner, pipeline orchestration
│   │   │   ├── core/             # Pure logic, NO I/O. Heavily unit tested
│   │   │   │   ├── money.py
│   │   │   │   ├── normalize.py
│   │   │   │   ├── validate.py   # Validation rules
│   │   │   │   ├── dedupe.py
│   │   │   │   ├── match.py      # 3-way matching
│   │   │   │   ├── exceptions.py # Taxonomy, explanations, suggested fixes
│   │   │   │   └── routing.py    # Straight-through vs review decision
│   │   │   ├── extract/
│   │   │   │   ├── pages.py      # Render PDFs/images, text layer
│   │   │   │   ├── schema.py     # InvoiceExtraction Pydantic model
│   │   │   │   ├── prompts/      # Versioned prompt files (v1.md, v2.md…)
│   │   │   │   ├── llm.py        # Claude client wrapper: budgets, retries, logging
│   │   │   │   └── confidence.py
│   │   │   ├── audit/            # Append-only audit event writer
│   │   │   ├── connectors/       # Export interface + CSV/JSON implementations
│   │   │   └── ingest/           # Ingestion interface + upload adapter
│   │   └── tests/
│   │       ├── unit/             # core/ logic, no DB, no network
│   │       ├── integration/      # DB + pipeline with recorded LLM responses
│   │       └── fixtures/
│   └── web/
│       ├── package.json
│       └── src/
│           ├── app/              # Routes: /queue, /invoices/[id], /dashboard, /upload
│           ├── components/
│           └── lib/api/          # Generated types + typed fetch client
├── data/
│   ├── generator/                # Synthetic invoice/PO/receipt generator
│   ├── seed/                     # Generated demo dataset (committed, small)
│   └── golden/                   # Eval set: documents + ground-truth JSON
├── eval/
│   ├── run_eval.py
│   └── reports/                  # Dated eval reports (markdown)
└── docs/
    ├── architecture.md
    ├── data-handling.md
    ├── runbook.md
    ├── exception-taxonomy.md
    ├── demo-script.md
    └── decisions/                # ADRs
```

**The rule for `core/`:** pure functions only. No database, no network, no file I/O. Everything that decides something (validation, matching, dedupe, routing) lives here and is unit tested. This is what makes the system testable and trustworthy.

---

## 5. Data model

### 5.1 Tables

Every business table has a `tenant_id` (UUID, not null). Demo A uses a single tenant, but this makes multi-client support a later configuration change instead of a rewrite.

| Table | Purpose | Key columns |
|---|---|---|
| `tenants` | One row per client (one for the demo) | `id`, `name`, `settings` (JSONB: thresholds, tolerances) |
| `suppliers` | Supplier master data | `id`, `name`, `aliases[]`, `tax_id`, `bank_account_hash`, `default_currency`, `is_active` |
| `purchase_orders` | POs | `id`, `po_number`, `supplier_id`, `currency`, `total_minor`, `status` |
| `po_lines` | PO lines | `po_id`, `line_no`, `description`, `sku`, `qty`, `unit_price_minor` |
| `goods_receipts` | What was actually received | `id`, `po_id`, `received_at` |
| `receipt_lines` | Received quantities | `receipt_id`, `po_line_id`, `qty_received` |
| `documents` | Uploaded files | `id`, `file_sha256` (unique per tenant), `filename`, `mime`, `page_count`, `storage_path`, `source`, `doc_quality` (`clean`/`scanned`/`photo`/`unknown`) |
| `invoices` | One per processed document | `id`, `document_id`, `status`, `supplier_id` (nullable), header fields, `subtotal_minor`, `tax_minor`, `total_minor`, `currency`, `po_number`, `route` (`straight_through`/`review`) |
| `invoice_lines` | Extracted lines | `invoice_id`, `line_no`, `description`, `qty`, `unit_price_minor`, `amount_minor`, `matched_po_line_id` |
| `field_extractions` | Per-field value + evidence | `invoice_id`, `field`, `raw_value`, `normalized_value`, `confidence`, `signals` (JSONB), `page`, `corrected_value`, `corrected_by` |
| `check_results` | Output of every check | `invoice_id`, `check_code`, `passed`, `details` (JSONB), `rule_version` |
| `exceptions` | Open or closed exceptions | `invoice_id`, `code`, `severity`, `explanation`, `suggested_fix`, `status` (`open`/`resolved`/`dismissed`), `resolved_by`, `resolution_note` |
| `review_actions` | Human decisions | `invoice_id`, `user_id`, `action` (`approve`/`reject`/`correct_field`/`dismiss_exception`/`request_info`), `payload`, `created_at` |
| `audit_events` | **Append-only** history | `id`, `tenant_id`, `invoice_id`, `actor_type` (`system`/`agent`/`user`), `actor_id`, `event_type`, `data` (JSONB), `created_at` |
| `llm_calls` | Every model call | `invoice_id`, `model`, `prompt_version`, `input_tokens`, `output_tokens`, `cost_usd_micros`, `latency_ms`, `status`, `request_hash` |
| `jobs` | Work queue | `id`, `type`, `payload`, `status`, `attempts`, `run_after`, `locked_at`, `last_error` |
| `eval_runs` | Evaluation results | `id`, `git_sha`, `model`, `prompt_version`, `metrics` (JSONB), `created_at` |

**Audit log rule:** `audit_events` is insert-only. There is no update or delete path in the code. Add a database trigger that raises an error on `UPDATE` or `DELETE` of that table.

### 5.2 Invoice statuses

```
received ─▶ extracting ─▶ extracted ─▶ checking ─┬─▶ cleared ──────────▶ approved ─▶ exported
                │                                 │   (straight-through)     ▲
                ▼                                 └─▶ needs_review ──────────┤
             failed ──(retry)──▶ extracting                  │              │
                                                             └─▶ rejected   │
                                               (human approves) ────────────┘
```

- **cleared** means all checks passed and confidence is high. Depending on the tenant setting `auto_approve_cleared` (default **false** in the demo), it either waits for one-click approval or moves to `approved` automatically.
- **Invoices above `approval_amount_limit` always need a human**, even if cleared.
- Every status change writes an `audit_events` row.

### 5.3 Extraction schema (`InvoiceExtraction`)

Header fields:
`supplier_name`, `supplier_tax_id`, `supplier_address`, `supplier_bank_account`, `invoice_number`, `invoice_date`, `due_date`, `po_number`, `currency`, `subtotal`, `tax_total`, `total`, `payment_terms`.

Line fields: `description`, `sku`, `quantity`, `unit_price`, `amount`, `tax_rate`.

For **every** field, the model returns:
- `value` (string or null; never invent a value),
- `self_confidence` (`high`/`medium`/`low`),
- `page` (1-based).

The prompt must tell the model: *if a field is not present, return null. Do not guess.* A test must verify that a document with no PO number produces `po_number: null`.

---

## 6. The pipeline, stage by stage

Each stage is a function that takes the invoice state and returns a new state plus audit events. Stages are **idempotent**: running one twice produces the same result.

### 6.1 Ingest
- Compute the SHA-256 of the file. If the same file was already uploaded for this tenant, link to the existing document and log `duplicate_file_upload`. This is an exact-copy check, separate from the duplicate *invoice* check.
- Allowed types: PDF, PNG, JPG, TIFF. Max size and page count come from config (default 15 MB and 10 pages). Reject anything else with a clear error.
- Render each page to an image (`pypdfium2`, 200 DPI) and extract the text layer (`pdfplumber`).
- Classify `doc_quality`: a usable text layer means `clean`; no text layer from a PDF means `scanned`; an image upload means `photo` (heuristic, can be overridden).

### 6.2 Extract
- Send the page images, plus the text layer when present, to the model with the versioned prompt and forced JSON schema.
- **Budgets** (all from config): max tokens per document, max pages, a per-day spend cap. If the daily cap is reached, pause extraction jobs and show a banner in the UI. Never fail silently.
- **Cache:** the key is `(file_sha256, model, prompt_version)`. Re-running the pipeline must not pay twice.
- **Retries:** at most 2, with backoff, only for transient errors. If the schema validation fails, retry once with the validation error included, then mark the invoice `failed` with the reason.
- Log every call in `llm_calls`.

### 6.3 Normalize and score confidence
- Normalize dates to ISO format, money to integer minor units, currency to an ISO 4217 code, and supplier names to trimmed, case-folded form for matching.
- **Field confidence** is a number from 0 to 1 that combines these signals:
  1. The model's `self_confidence`.
  2. **Text-layer agreement:** the normalized value appears in the page's text layer. This only applies to `clean` documents.
  3. **Rule support:** for example, the total is supported when the line amounts add up to it.
  4. **Master-data support:** for example, the supplier tax ID matches a known supplier.
- Store every signal in `field_extractions.signals`, so the UI can show *why* a field is trusted.
- The thresholds `field_confidence_min` (default 0.8) and the list of critical fields (`supplier`, `invoice_number`, `invoice_date`, `total`, `currency`) come from config.

### 6.4 Validate
Deterministic rules in `core/validate.py`. Each rule has a code, a version, and unit tests. See the exception codes in section 7.
- Line amounts ≈ quantity × unit price (tolerance: 1 minor unit per line).
- The lines add up to the subtotal, and subtotal + tax = total (tolerance from config).
- The invoice date is not in the future and not older than `max_invoice_age_days`.
- The due date is on or after the invoice date.
- The currency is valid and matches the supplier's default or the PO.
- The supplier is known (exact tax ID match, else a fuzzy name/alias match ≥ 90).
- **The bank account differs from the supplier master.** This is a fraud signal and always goes to review, whatever the confidence.

### 6.5 Detect duplicates
In `core/dedupe.py`, compare against existing invoices for the same tenant:
- **Hard duplicate:** same supplier, same normalized invoice number.
- **Soft duplicate:** same supplier, same total, invoice date within ±7 days, and invoice number similarity ≥ 85 (this catches `INV-1043` vs `INV1043` vs `1043`).
- Record which existing invoice it collides with, so the reviewer can compare them side by side.

### 6.6 Match (3-way)
In `core/match.py`:
- Find the PO by `po_number`. If none is given, try the supplier's open POs whose total is within tolerance and flag the result as *inferred*.
- Match invoice lines to PO lines using SKU first, then description similarity, then amount.
- Compare quantity and price against configurable tolerances (default: price ±2%, quantity exact).
- Compare the billed quantity against the **received** quantity from goods receipts.
- Track the cumulative amount billed against each PO to catch over-billing across several invoices.

### 6.7 Classify exceptions and route
- Turn every failed check into an `exceptions` row using the taxonomy (section 7).
- **Explanations are generated from templates**, deterministically from the check details. They are not free-form model output, which keeps them accurate and consistent. (Optional later: use the model to polish wording, but always show the underlying facts.)
- **Routing** (`core/routing.py`) sends the invoice to `cleared` only if:
  - there are no open exceptions of severity `review` or `block`, and
  - every critical field's confidence is ≥ `field_confidence_min`, and
  - the total is ≤ `approval_amount_limit`.

  Otherwise it goes to `needs_review`.

---

## 7. Exception taxonomy

Keep this table in `docs/exception-taxonomy.md` and in code as a single enum. **Every code must have an explanation template, a suggested fix, and a test.**

| Code | Severity | Example explanation | Suggested fix |
|---|---|---|---|
| `UNREADABLE_DOCUMENT` | block | "The file couldn't be read clearly (page 2 is blank or too blurry)." | Ask the supplier for a clearer copy |
| `LOW_CONFIDENCE_FIELD` | review | "We're not sure about the **total**: it wasn't found in the document's text layer." | Check the highlighted field and confirm or correct it |
| `LINE_MATH_MISMATCH` | review | "Line 3: 12 × $45.00 should be $540.00, but the invoice says $504.00." | Confirm with the supplier; possibly a typo |
| `TOTAL_MISMATCH` | review | "The lines add up to $2,140.00, but the invoice total is $2,410.00." | Ask the supplier for a corrected invoice |
| `TAX_MISMATCH` | review | "Tax is $318.00, but 15% of $2,140.00 is $321.00." | Check the tax rate for this supplier |
| `INVALID_DATE` | review | "The invoice date is 3 months in the future." | Confirm the date with the supplier |
| `UNKNOWN_SUPPLIER` | review | "'Acme Supplies Ltd' doesn't match any known supplier. Closest match: 'ACME Supply Co.' (82%)." | Link to an existing supplier or create a new one |
| `BANK_DETAILS_CHANGED` | block | "The bank account on this invoice is different from the one on file for this supplier." | **Verify by phone using a known contact before approving.** This is a common fraud pattern |
| `POSSIBLE_DUPLICATE` | block | "This looks like invoice INV-1043 from the same supplier, received 4 days ago, for the same amount." | Compare them side by side; reject if duplicate |
| `NO_PO` | review | "This invoice has no PO number, and no open PO for this supplier matches the amount." | Ask the requester for the PO, or approve as a non-PO invoice |
| `PO_NOT_FOUND` | review | "PO-7781 isn't in the system." | Check for a typo or ask purchasing |
| `PRICE_VARIANCE` | review | "Line 2 is billed at $48.00 per unit, but the PO says $45.00 (+6.7%, limit 2%)." | Accept the variance, or ask the supplier to correct it |
| `QTY_VARIANCE` | review | "Line 1 bills 120 units, but the PO line is for 100." | Ask the supplier or purchasing |
| `RECEIPT_MISSING` | review | "Nothing has been received yet for PO-7781." | Wait for the goods receipt or confirm delivery with the requester |
| `QTY_NOT_RECEIVED` | review | "Invoice bills 100 units; only 80 have been received." | Hold, or pay partially once the rest arrives |
| `PO_OVERBILLED` | block | "Invoices against PO-7781 now total $10,450, which is more than the PO total of $9,800." | Stop and check with purchasing |
| `CURRENCY_MISMATCH` | review | "The invoice is in EUR, but the PO is in USD." | Confirm the currency with the supplier |
| `ABOVE_APPROVAL_LIMIT` | review | "The total of $25,000 is above the $10,000 auto-approval limit." | Needs manager approval |

Severity meanings:
- `info`: shown, but doesn't stop the invoice.
- `review`: needs a person.
- `block`: needs a person **and** an explicit resolution note before approval.

---

## 8. Evaluation (how we prove it works)

This is what turns Demo A from "a demo" into "a reference build we can publish numbers for."

### 8.1 Datasets

1. **Synthetic demo set (`data/seed/`):** about 120 invoices from about 15 suppliers, with matching POs and receipts. It's generated by `data/generator/` using `reportlab` for PDFs, with:
   - 4–6 different invoice layouts (varied field labels, table styles, languages of labels like "Inv No" / "Invoice #" / "Bill No"),
   - a quality mix of about 50% clean PDFs, 30% "scanned" (rendered to an image, then rotated, noised, and blurred), and 20% "photo" (perspective warp, uneven lighting),
   - **planted problems** in known proportions: duplicates, math errors, price and quantity variances, missing receipts, unknown suppliers, one changed bank account, and one over-billed PO,
   - a ground-truth JSON file for every document.
2. **Golden eval set (`data/golden/`):** a fixed subset of about 60 documents that is never used while writing prompts. It can include a small number of **properly licensed** public invoice or receipt samples; record each source and its licence in `data/golden/SOURCES.md`. If the licence is unclear, don't use it.

### 8.2 Metrics (reported per field and per `doc_quality`)

| Metric | Definition |
|---|---|
| Field accuracy | Exact match after normalization, per field |
| Line-item F1 | Matched lines vs ground truth (a line matches if its amount and quantity are correct) |
| Exception recall | % of planted problems that raised the right exception code |
| Exception precision | % of raised exceptions that were real problems |
| Touchless rate | % of invoices routed `cleared` |
| False clear rate | % of invoices routed `cleared` that actually had a problem. **This must be 0% on the golden set.** |
| Cost per invoice | Mean model cost (USD) |
| Latency | p50 / p95 processing time per invoice |

**The false clear rate is the most important number.** A wrongly cleared invoice is the failure clients fear most. Tune thresholds so we would rather send too many invoices to review than let one bad invoice through.

### 8.3 How evaluation runs

- `make eval` runs the full golden set with real model calls and writes `eval/reports/YYYY-MM-DD-<git-sha>.md` plus an `eval_runs` row.
- CI runs a **small eval subset using recorded model responses** (stored fixtures keyed by request hash), so CI is free and deterministic.
- **Regression gate:** a PR that changes a prompt, model, threshold, or rule must include a fresh eval report in the PR description. Any drop greater than 2 points on a critical field, or any false clear, blocks the merge.

---

## 9. Milestones (copy each into a GitHub issue)

Milestones are in order. Each ends with something you can see working. Estimated effort assumes one developer working with Claude Code.

---

### M0 — Project skeleton
**Goal:** anyone can clone the repo and run it with one command.
- Create the monorepo structure from section 4.
- Docker Compose with `db` (Postgres), `api`, `worker`, and `web`.
- FastAPI app with `/health`, settings from `.env`, and `.env.example` documenting every variable.
- Next.js app with a placeholder page that calls `/health`.
- Alembic set up, with an initial empty migration.
- CI (GitHub Actions): ruff, mypy (strict for `core/`), pytest, eslint, typecheck, and web build.
- `CLAUDE.md` (content in section 11.3), a README with run instructions, and a PR template.

**Acceptance criteria**
- [ ] `docker compose up` shows the web page saying "API: healthy".
- [ ] CI is green on `main`.
- [ ] A fresh clone plus the README steps works on another teammate's machine.

---

### M1 — Data model and synthetic data
**Goal:** a realistic, messy demo world exists in the database.
- Migrations for every table in section 5.1, including the `tenant_id` columns and the audit-log trigger.
- `data/generator/`: suppliers, POs, receipts, and invoices with the layouts, quality mix, and planted problems from section 8.1, plus a ground-truth JSON file per invoice.
- A `make seed` command that loads the master data (suppliers, POs, receipts) and copies the invoice files into an "inbox" folder ready to upload.
- A generated `data/seed/MANIFEST.md` listing each invoice, its quality, and which planted problems it contains.

**Acceptance criteria**
- [ ] `make seed` works on an empty database and can be re-run safely.
- [ ] The manifest shows every exception code from section 7 represented at least once (except `UNREADABLE_DOCUMENT`, which gets 2 deliberately bad files).
- [ ] A test proves the audit-log trigger blocks `UPDATE` and `DELETE`.

---

### M2 — Ingestion and job queue
**Goal:** uploading a file creates an invoice and starts processing.
- `POST /documents` (multipart) with type and size validation, SHA-256 dedupe, and storage.
- Page rendering, text-layer extraction, and `doc_quality` classification.
- A Postgres job queue with worker, retries, and visibility into stuck jobs.
- An `Ingestor` interface with an upload adapter; stub a `FolderIngestor` for the demo inbox.
- Audit events for `document_received` and `status_changed`.

**Acceptance criteria**
- [ ] Uploading a PDF creates `documents` and `invoices` rows and a job; the worker picks it up.
- [ ] Uploading the same file twice doesn't create a second document.
- [ ] Unsupported or oversized files are rejected with a clear message.

---

### M3 — Extraction
**Goal:** invoices are read into structured fields.
- `extract/schema.py` (section 5.3) and `prompts/v1.md`.
- `extract/llm.py`: the model call with forced schema, token and page budgets, a daily spend cap, cache, retries, and `llm_calls` logging.
- Normalization in `core/normalize.py` and `core/money.py`, with unit tests for dates, currencies, amounts, and number formats (`1.234,56` vs `1,234.56`).
- Confidence scoring in `extract/confidence.py` (section 6.3).
- Recorded-response fixtures so integration tests make no network calls.

**Acceptance criteria**
- [ ] The pipeline fills `invoices`, `invoice_lines`, and `field_extractions` for all clean seed invoices.
- [ ] A missing PO number comes back as `null`, not a made-up value (tested).
- [ ] Re-processing the same file uses the cache (no new `llm_calls` row, verified by test).
- [ ] Hitting the daily spend cap pauses jobs and is visible through the API.

---

### M4 — Validation rules
**Goal:** the math, dates, supplier, and bank details are checked.
- Implement the rules from section 6.4 in `core/validate.py`, each with a code and version.
- Write results to `check_results`.

**Acceptance criteria**
- [ ] Every rule has unit tests for pass, fail, and edge cases (rounding, zero tax, credit notes with negative totals).
- [ ] All planted math, date, supplier, and bank problems in the seed data are caught.

---

### M5 — Duplicate detection
**Goal:** likely duplicates are caught before approval.
- Implement hard and soft matching (section 6.5) in `core/dedupe.py`.

**Acceptance criteria**
- [ ] All planted duplicates are detected, including number-format variants.
- [ ] No false duplicates across the rest of the seed set.

---

### M6 — 3-way matching
**Goal:** invoices are compared with POs and receipts.
- Implement section 6.6 in `core/match.py`, with tolerances from tenant settings.

**Acceptance criteria**
- [ ] All planted PO, price, quantity, receipt, and over-billing problems are detected.
- [ ] Line matching works even when the invoice line descriptions differ slightly from the PO.
- [ ] Unit tests cover partial receipts and multiple invoices against one PO.

---

### M7 — Exceptions and routing
**Goal:** every problem has a plain-language explanation and a suggested fix, and every invoice is routed.
- Implement the taxonomy (section 7) with templates, and routing (section 6.7).
- Write `docs/exception-taxonomy.md`.

**Acceptance criteria**
- [ ] Every code has a template, a suggested fix, and a test.
- [ ] Every explanation contains the real numbers from the check, never placeholders.
- [ ] Routing tests cover every rule in 6.7, including the approval limit and the bank-details block.

---

### M8 — Review queue UI
**Goal:** a reviewer can clear the queue quickly and confidently.
- `/queue`: a list of invoices needing review, sorted by severity then age. Filters by exception code, supplier, and status. It shows the supplier, total, and top exception at a glance.
- `/invoices/[id]`: the document viewer (page images) side by side with the extracted fields. Low-confidence fields are highlighted with the reason. Exceptions are shown as cards with an explanation, a suggested fix, and actions.
- Actions: correct a field (re-runs checks), resolve or dismiss an exception with a note (required for `block`), approve, reject with a reason, and "request info" (marks it as waiting and logs it).
- Duplicate exceptions show both invoices side by side.
- Bank details are masked except for the last 4 digits; revealing them is logged.
- A simple demo login with one "reviewer" user; no public access.

**Acceptance criteria**
- [ ] A reviewer can process every seed exception without leaving the UI.
- [ ] Correcting a field re-runs the checks and updates the exceptions immediately.
- [ ] Approval is blocked while `block`-severity exceptions are unresolved.
- [ ] Usable at 375 px width (a phone) with no horizontal scrolling.
- [ ] A Playwright smoke test covers upload → review → approve.

---

### M9 — Audit timeline
**Goal:** every invoice has a complete, readable history.
- `/invoices/[id]` gets a timeline tab showing each event with its actor (system, agent, or user), time, and a plain-language summary. Expanding an event shows the raw details.
- `GET /invoices/{id}/audit` returns the JSON history.

**Acceptance criteria**
- [ ] Every status change, check, exception, human action, and model call appears in the timeline.
- [ ] The history can be exported as JSON.

---

### M10 — Evaluation harness
**Goal:** we can publish honest accuracy numbers.
- Implement section 8: `make eval`, the report generator, CI subset with recorded responses, and the regression gate documented in the PR template.

**Acceptance criteria**
- [ ] The first full eval report is committed in `eval/reports/`.
- [ ] The false clear rate on the golden set is 0%.
- [ ] The report splits accuracy by field and by `doc_quality`.

---

### M11 — Metrics dashboard
**Goal:** show the business value at a glance.
- `/dashboard` with invoices processed, touchless rate, exceptions by code (bar chart), average time in review, model cost per invoice, and estimated savings using a configurable "manual cost per invoice" (default $12, clearly labelled as an assumption).
- `GET /metrics` endpoint.

**Acceptance criteria**
- [ ] The numbers match the database (tested with a fixed dataset).
- [ ] Every assumption is labelled on screen.

---

### M12 — Export and connector interface
**Goal:** approved invoices can leave the system, and real accounting systems can be added later without a rewrite.
- A `Connector` interface: `export_invoice(invoice) -> ExportResult` and `health() -> ConnectorHealth`.
- Implementations: `CsvConnector` and `JsonConnector`.
- Export happens **only** for `approved` invoices, and each export is logged. The status becomes `exported` with the connector reference.
- `docs/connectors.md` explains how to add a new connector, with QuickBooks/Xero as the example.

**Acceptance criteria**
- [ ] Exporting a non-approved invoice is impossible (tested at both the API and core levels).
- [ ] The CSV opens cleanly in a spreadsheet with correct amounts and dates.

---

### M13 — Demo polish, docs, and recording
**Goal:** Demo A is ready to show clients.
- Write `docs/demo-script.md` (section 13) and rehearse it.
- A `make demo-reset` command that resets to a clean seeded state.
- Final docs: README, architecture, data handling, and runbook.
- Record the walkthrough video.

**Acceptance criteria**
- [ ] The demo runs start to finish from `make demo-reset` without surprises, 3 times in a row.
- [ ] The Definition of Done in section 2 is fully checked.

---

### Suggested order and rough timing

| Week | Milestones | Visible result |
|---|---|---|
| 1 | M0, M1 | Repo runs; a messy demo world exists |
| 2 | M2, M3 | Uploads are read into structured data |
| 3 | M4, M5, M6 | Problems are caught |
| 4 | M7, M8 | Reviewers can work the queue |
| 5 | M9, M10, M11 | Audit trail, honest numbers, dashboard |
| 6 | M12, M13 | Export, polish, video |

These are chapters, not deadlines. Steady beats rushed.

---

## 10. Security and data handling

Demo A only ever uses synthetic or properly licensed data, but we build it as if it held real client data, because it will.

- **Secrets:** only in `.env` (git-ignored). Never in code, logs, fixtures, or screenshots.
- **Bank details:** stored encrypted at rest (application-level encryption with a key from env). Only a hash is used for comparison, and they are masked in the UI.
- **Logs:** never log full document text, bank details, or model prompts containing document content at INFO level. Use DEBUG, which is off by default.
- **Model provider:** record the provider's data-retention and training terms (as found in their current documentation) in `docs/data-handling.md`. This page is reused on the website's Data Handling page.
- **Uploads:** validate the file type by content, not only by extension; enforce size and page limits; never execute or render uploaded HTML.
- **Access:** the demo requires login. There are no public endpoints except `/health`.
- **Dependencies:** enable Dependabot, and keep lock files committed.

---

## 11. How we build with Claude Code

### 11.1 Team rules (from the BeyondAI plan)

1. One task at a time, each written as a clear GitHub issue (use the milestones above).
2. Claude Code works on its own branch for each task: `feat/m3-extraction`, `fix/dedupe-number-format`, etc.
3. **Whoever runs the task reads every change. If you can't explain it, don't merge it.**
4. A teammate reviews each pull request, and Dipto checks the preview.
5. Documentation is updated in the same pull request as the code.

### 11.2 Working a milestone with Claude Code

1. Paste the milestone issue into Claude Code and point it to this playbook and `CLAUDE.md`.
2. Ask Claude Code to **propose a plan first**: which files it will create or change, which tests it will write, and any questions. Review the plan before any code is written.
3. Build in small steps. For logic in `core/`, write the tests first.
4. Run `make check` (lint, types, tests) before opening the PR.
5. PR description: what changed, how it was tested, screenshots for UI, and an eval report if prompts, models, thresholds, or rules changed.

### 11.3 `CLAUDE.md` (create this in M0)

```markdown
# CLAUDE.md — rules for working in this repo

## Always
- Read docs/playbook.md (this playbook) and the current GitHub issue before changing code.
- Work only on the current issue. If you find other problems, list them in the PR; don't fix them.
- Propose a plan and wait for approval before large changes.
- Keep all decision logic (validation, dedupe, matching, routing) in apps/api/src/intake/core/ as pure functions with unit tests.
- Money is integer minor units + ISO currency. Never floats.
- Every state change writes an audit event.
- Update docs in the same PR as code.
- Run `make check` before saying a task is done.

## Never
- Never add code that pays, transfers, or moves money.
- Never update or delete audit_events.
- Never commit real client data, secrets, or .env files.
- Never call the model API in unit or CI tests; use recorded fixtures.
- Never change a prompt, model, threshold, or rule without running `make eval` and reporting the result.
- Never guess an API detail for the Anthropic SDK; check https://docs.claude.com.

## Commands
- make dev        # start everything
- make seed       # load demo data
- make check      # lint + types + tests
- make eval       # full evaluation (costs money; ask first)
- make demo-reset # reset to the clean demo state
```

### 11.4 Code conventions

- **Python:** ruff (lint + format), type hints everywhere, mypy strict on `core/`. Pydantic models at every boundary (API, model output, config).
- **TypeScript:** strict mode, no `any`, API types generated only from OpenAPI (`pnpm gen:api`).
- **Naming:** exception codes in `UPPER_SNAKE_CASE`; statuses in `lower_snake_case`; DB tables plural.
- **Tests:** `core/` coverage ≥ 90%; every bug fix starts with a failing test.
- **Commits:** conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).

---

## 12. Built to grow: what makes Demo A reusable

These seams are what let Demo A become the base for real client builds:

| Seam | Demo A | Later |
|---|---|---|
| `tenant_id` everywhere | One tenant | Many clients, isolated data |
| `Ingestor` interface | Upload + folder | Email inbox, SFTP, accounting-system pull |
| `Connector` interface | CSV/JSON | QuickBooks, Xero, NetSuite, SAP |
| Tenant `settings` JSONB | Demo defaults | Per-client tolerances, limits, critical fields |
| Versioned prompts + eval | One golden set | A per-client golden set built during Blueprint |
| `llm_calls` table | Cost display | Per-client cost limits and billing (Keel) |
| Audit log + metrics | Demo timeline | Monthly Keel care reports |

**How this ties to the services:**
- **Blueprint:** run Demo A on a client's own invoices, then produce the eval report and exception breakdown.
- **Shift:** turn Demo A into a client-specific build.
- **Switchboard:** real connectors.
- **Keel:** eval runs, cost limits, and monthly monitoring.

---

## 13. Demo script (2–3 minutes)

1. **The problem (15 s):** "AP teams don't lose time typing. They lose it on exceptions." Show the dashboard with the exceptions-by-reason chart.
2. **Upload a batch (20 s):** drag in 10 mixed invoices, including a crumpled "photo" one. Show them processing.
3. **Clean invoice (15 s):** open one that cleared, with every field green and the evidence visible.
4. **The exceptions (60 s):**
   - A price variance, with the explanation showing the real numbers and the suggested fix.
   - A possible duplicate, shown side by side; reject it.
   - The changed bank account, which is blocked and requires a note: "This is how invoice fraud is caught."
5. **Correct a field (15 s):** fix a low-confidence value and watch the exceptions update.
6. **Audit trail (15 s):** show the full history of one invoice.
7. **Honest numbers (20 s):** show the eval report with accuracy by field and document quality, and a 0% false clear rate.
8. **Close (10 s):** "We can run this on 30 of your own invoices in a free audit."

---

## 14. Templates

### ADR (`docs/decisions/NNNN-title.md`)

```markdown
# NNNN — Title
Date: YYYY-MM-DD · Status: proposed | accepted | superseded

## Context
What problem or choice are we facing?

## Decision
What we chose.

## Why
The main reasons, and the options we rejected.

## Consequences
What gets easier, what gets harder, and what we must watch.
```

### Pull request

```markdown
## What changed
## Why (link the issue)
## How it was tested
## Screenshots (UI changes)
## Eval report (required if prompts, models, thresholds, or rules changed)
## Docs updated
- [ ] Yes / not needed because…
```

---

## 15. Open decisions (settle these in Sprint 1)

| # | Decision | Suggested default | Owner |
|---|---|---|---|
| 1 | Which Claude model for extraction | Start mid-tier; compare with the eval harness | Team |
| 2 | Where the preview is hosted | Web on Vercel; API, worker, and DB on a small container host | Team |
| 3 | Demo login method | Simple email + password for one reviewer account | Team |
| 4 | Target currencies and number formats | USD, EUR, GBP to start | Dipto |
| 5 | Invoice languages in the synthetic set | English only for v1 | Dipto |
| 6 | Whether to add real licensed public samples to the golden set | Only if the licence is clearly OK | Team |

---

*BeyondAI · Demo A developer playbook. Questions or ideas? Raise them in the issue, or bring them to the next sprint review.*
