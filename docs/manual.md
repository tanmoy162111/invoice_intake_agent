# Invoice Intake Agent: Manual

> **Living document.** Updated in every milestone pull request, as new abilities appear.
> **Describes:** the system after M3 (extraction), 2026-09-25.
> Companion: [`report.md`](report.md) explains what was built and why. This manual explains how to
> *use and run* it.

## How to read this manual

Each section is written in layers, so you can stop when you have what you need:

- **In plain words** is for anyone.
- **Steps** are for people who run the system but are not programmers. Copy and paste the commands.
- **Technical notes** are for engineers.

Things that do not exist yet are marked **[Coming in M#]** so nothing here promises what is not built.

---

## 1. What this system is

**In plain words.** A helper for the accounts-payable team. Suppliers send invoices. The helper reads
each one, checks it against what was ordered and delivered, and points out anything that looks wrong,
explaining *what* and *what to do*. A person makes every final decision. **The system never pays
anyone.**

### Who uses it

| Role | What they do | Available |
|---|---|---|
| **Reviewer** (AP clerk) | Works the queue of invoices that need a person; corrects, approves, rejects | [Coming in M8] |
| **Approver / manager** | Approves invoices above the limit | [Coming in M8] |
| **Operator** (IT or support) | Starts the system, loads data, watches the background jobs | Today |
| **Developer** | Builds and tests the system | Today |

**Today, the only way in is the API** (a set of web addresses a program or the `curl` command can call).
The reviewer screen arrives in M8. The web page you can open now only shows "API: healthy".

---

## 2. Ideas you need

### An invoice's life

Every invoice moves through these steps. Each change is written to a permanent history.

| Status | Plain meaning | Reached by |
|---|---|---|
| `received` | We have the file and it is waiting to be read | Today (M2) |
| `extracting` / `extracted` | Fields are being read / have been read | Today (M3) |
| `checking` | The checks are running | [M4 to M7] |
| `cleared` | All checks passed and confidence is high; waits for one-click approval | [M7] |
| `needs_review` | A person must look | [M7] |
| `approved` / `rejected` | A person decided | [M8] |
| `exported` | Handed to the accounting system | [M12] |
| `failed` | Reading failed, with a reason (section 5.6); can be retried | Today (M3) |

An invoice above the approval limit always needs a person, even if every check passed.

### Exceptions

An **exception** is a problem the system found, for example "price is 6.7% above the purchase order".
Each has a **severity**:

- **info**: shown, does not stop the invoice.
- **review**: a person must look.
- **block**: a person must look **and write a note** before approving (used for the riskiest cases,
  like a changed bank account).

### Confidence and document quality

- **Confidence** says how sure the system is about each field it read (0 to 100%). It combines four
  signals: the model's own opinion, whether the value appears in the document's text, whether the
  arithmetic supports it, and whether the supplier is already known. The model's opinion is never
  enough on its own, and any signal that *contradicts* a value keeps it under 80%. Low confidence
  will send the invoice to a person once routing exists (M7). **Uncertain means human.**
- **Not found is not guessed.** A value that is not on the invoice is stored as empty. A value that
  cannot be read without guessing (a date like 03/04/2026, a quantity like 1.200, a bare `$` from a
  supplier that normally bills in another currency) is also left empty, with confidence 0.
- **Document quality** describes the file: `clean` (a digital PDF with real text), `scanned` (a picture
  of a page), `photo` (a phone picture), `unknown` (blank or unusable). Accuracy is always reported
  separately for each, because a photo is harder than a clean PDF.

### The history (audit log)

Everything that happens to an invoice is recorded: who or what did it, when, and why. The record
**cannot be edited or deleted**; the database refuses.

---

## 3. Getting started (about 10 minutes)

### What you need
- A computer with **Docker** (with Compose), and for local development also **Node 24**, **pnpm** and
  **uv**.
- No accounts and no real data. Everything uses made-up documents.

### Start it

Open a terminal in the project folder.

```bash
make dev
```

This creates a private settings file (`.env`) with generated secrets the first time, then starts the
database, the API, the worker and the web page. Open **http://localhost:3000**. Seeing
**"API: healthy"** means the parts are talking.

> **A port is already in use?** Add lines like `API_PORT=8001`, `WEB_PORT=3001`, `DB_PORT=5434` to the
> `.env` file and run `make dev` again.

### Load the demo world

In a second terminal:

```bash
make seed            # 15 suppliers, 104 purchase orders, receipts; puts 120 invoice files in data/inbox/
make ingest-inbox    # feeds those 120 files into the system
```

The worker then processes them in the background (about 30 seconds for all 120).

---

## 4. Guides for today

### 4.1 Upload one invoice

**Steps.** Your access token is in the `.env` file (the line starting `API_TOKEN=`).

```bash
TOKEN=$(grep '^API_TOKEN=' .env | cut -d= -f2-)
curl -H "Authorization: Bearer $TOKEN" -F "file=@my-invoice.pdf" http://localhost:8000/documents
```

Use your own `API_PORT` if you changed it.

**What you get back.** A small record with the document id and invoice id.

| Response | Meaning |
|---|---|
| **201** | New file accepted; a background job was queued |
| **200** with `"duplicate": true` | This exact file was uploaded before; nothing new was created |
| **413 / 415 / 422** | File refused; the reply says why and how to fix it (section 5.1) |
| **401** | Missing or wrong token |

### 4.2 Check what happened

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/documents/<document-id>
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/jobs
```

The first shows the document: its type, page count, `doc_quality` (filled in once the worker has run) and
the invoice status. The second shows the background queue: how many jobs are waiting, running, done or
failed, whether any are stuck, and the most recent failures.

### 4.3 Feed a folder of files

Put files in `data/inbox/` and run `make ingest-inbox`. Files are not moved or deleted. Running it again
is safe: files already seen are recognised and skipped. The summary reads "accepted N, already known M,
rejected K" and lists any rejected file with its reason.

### 4.4 Something looks stuck

1. Look at `/jobs`. If `queued` is growing, the worker may be down: `docker compose logs worker`, then
   `docker compose restart worker`.
2. If `stuck` is above 0, a job has been running too long. The worker's recovery step retries it
   automatically.
3. If `failed` lists jobs, they used all three attempts. The reason shown is the *kind* of error only.
   Fix the cause, then ask a developer to re-queue it (see [`runbook.md`](runbook.md)).

### 4.5 Stop, reset

```bash
docker compose down        # stop, keep data
docker compose down -v     # stop and erase the database and stored files
```

### 4.6 Turn reading on

Reading invoices needs a model. Set these in `.env`, then restart the worker
(`docker compose restart worker`):

| Setting | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your key from the Claude Console |
| `EXTRACTION_MODEL` | `claude-sonnet-5` (the default) |
| `BANK_ENCRYPTION_KEY` | Generated for you by `make dev` |

Until this is set, extraction jobs **wait** (they are not failed): `GET /extraction/status` says
`"configured": false`, and `GET /jobs` shows them under `paused`. Nothing is lost. Once configured, the
waiting jobs run on their own within a few minutes.

**Cost.** Model calls are the only part of the system that costs money. They are estimated (not yet
measured) at one to three US cents for a one-page invoice. A daily limit protects you (next guide). The
same file is never read twice: the answer is kept.

### 4.7 Watch the spending limit

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/extraction/status
```

| Field | Meaning |
|---|---|
| `configured` | A model, credentials and the bank key are set |
| `spent_today_usd_micros`, `daily_cap_usd_micros` | Spend since 00:00 UTC and the limit, in millionths of a dollar (1,000,000 = 1 USD) |
| `cap_reached` | `true`: reading is paused until `resumes_at` (next 00:00 UTC) |
| `paused_jobs` | Jobs waiting because of the limit or because reading is not configured |

Raise the limit with `DAILY_SPEND_CAP_USD` in `.env` and restart the worker; paused jobs then resume.
`DAILY_SPEND_CAP_USD=0` pauses all reading. The limit is checked before each call, so with several workers
it can be overshot by about one call each.

### 4.8 See what was read

There is no reviewer screen yet (M8). To look at one invoice, ask the database:

```bash
docker compose exec db psql -U intake -c "
  select field, raw_value, normalized_value, confidence, signals
  from field_extractions
  where invoice_id = '<invoice-id>' and field <> 'supplier_bank_account'
  order by field;"
```

Amounts appear in `normalized_value` as whole cents. `confidence` is 0 to 1. `signals` says *why*
(model opinion, text check, arithmetic, known supplier) or, for a value left empty, the reason
(section 5.7). Bank accounts are stored encrypted and are deliberately left out of this query. The
invoice's own columns (number, dates, totals, currency) and its lines are in `invoices` and
`invoice_lines`.

### 4.9 Try it with a local model (demo only)

To run the demo with nothing leaving your computer, install [Ollama](https://ollama.com), pull a vision
model, then set in `.env`: `LLM_PROVIDER=ollama`, `EXTRACTION_MODEL=<the model name from ollama list>`,
and inside Docker `OLLAMA_BASE_URL=http://host.docker.internal:11434`. Restart the worker.

Know the trade-offs: small local models read invoices noticeably worse than Claude, they are slower and
need a lot of memory, and **any accuracy you measure does not apply to the Claude-based system**. Invoice
pages are sent to whatever address `OLLAMA_BASE_URL` points at, so keep it on hardware you control.
Never use hosted free-tier models with real data: they may learn from what you send.

---

## 5. Reference

### 5.1 Why a file can be refused

| Code | HTTP | What it means | What to do |
|---|---|---|---|
| `UNSUPPORTED_FILE_TYPE` | 415 | Not a PDF, PNG, JPEG or TIFF (the system looks inside the file, so renaming does not help) | Convert it and upload again |
| `FILE_TOO_LARGE` | 413 | Over 15 MB | Compress or split; scan at lower resolution |
| `TOO_MANY_PAGES` | 422 | Over 10 pages | Upload only the invoice pages |
| `EMPTY_FILE` | 422 | The file has no content | Check it and upload again |
| `UNREADABLE_FILE` | 422 | Corrupted or password protected | Re-export or re-scan |

These refuse the *file*. A file that opens but is hard to read is accepted and later becomes an
invoice exception (section 6).

### 5.2 The API today

| Address | Purpose | Token needed |
|---|---|---|
| `GET /health` | Is the API alive? | No |
| `POST /documents` | Upload a file | Yes |
| `GET /documents/{document_id}` | Look up a document | Yes |
| `GET /jobs` | Queue health, including jobs paused on purpose | Yes |
| `GET /extraction/status` | Model spend against the daily limit, and whether reading is paused | Yes |
| `GET /openapi.json` | Machine-readable description of the API | Yes |

Send the token as `Authorization: Bearer <token>`. The token is a temporary arrangement until real
login arrives in M8. If no token is configured the server refuses everything except `/health`.

### 5.3 Commands

| Command | What it does |
|---|---|
| `make dev` | Start database, API, worker and web page |
| `make seed` | Load the demo master data; stage invoice files in `data/inbox/` (safe to repeat) |
| `make ingest-inbox` | Feed `data/inbox/` through ingestion |
| `make generate` | Rebuild the synthetic dataset (developers; existing golden files are kept) |
| `make check` | Run every automated quality check |
| `make gen-api` | Refresh the web app's API types |
| `make eval` | Accuracy evaluation [Coming in M10]; costs money, asks first |
| `make demo-reset` | Reset to the clean demo state [Coming in M13] |

### 5.4 Settings (in `.env`)

| Setting | Default | Meaning |
|---|---|---|
| `API_PORT`, `WEB_PORT`, `DB_PORT` | 8000, 3000, 5432 | Ports on your computer |
| `API_TOKEN` | generated | Access token for the API |
| `BANK_ENCRYPTION_KEY` | generated | Key that protects stored bank details |
| `MAX_UPLOAD_BYTES` | 15728640 | Largest accepted file (15 MB) |
| `MAX_PAGES` | 10 | Most pages accepted |
| `RENDER_DPI` | 200 | Sharpness of page images |
| `JOB_MAX_ATTEMPTS` | 3 | Tries before a job is marked failed |
| `JOB_BACKOFF_BASE_S` / `_CAP_S` | 10 / 600 | Wait before a retry: 10 s, 20 s, 40 s ... up to 10 min |
| `JOB_VISIBILITY_TIMEOUT_S` | 300 | When a running job counts as stuck |
| `WORKER_POLL_INTERVAL_S` | 2 | How often the worker looks for work |
| `LLM_PROVIDER` | `anthropic` | `anthropic`, or `ollama` (local, demo only) |
| `ANTHROPIC_API_KEY` | empty | Your key. Empty means reading is paused, not failed |
| `EXTRACTION_MODEL` | `claude-sonnet-5` | Which model reads invoices. Must have a known price, or it is refused |
| `DAILY_SPEND_CAP_USD` | 5 | Most model spend per UTC day. 0 pauses all reading |
| `EXTRACTION_PROMPT_VERSION` | `v1` | Which prompt file is used (`apps/api/src/intake/extract/prompts/`) |
| `EXTRACT_MAX_OUTPUT_TOKENS` | 8000 | Longest answer the model may give |
| `EXTRACT_MAX_INPUT_TOKENS` | 100000 | Largest estimated document; bigger is failed before any spend |
| `EXTRACT_TIMEOUT_S` | 120 | How long one model call may take before it counts as a failure to retry |
| `EXTRACT_NOT_CONFIGURED_RETRY_S` | 300 | How often a paused job checks whether reading has been configured |
| `FIELD_CONFIDENCE_MIN` | 0.8 | Below this a critical field counts as doubtful |
| `OLLAMA_BASE_URL`, `OLLAMA_NUM_CTX` | `http://localhost:11434`, 8192 | Local model address and context size |

`.env` holds secrets and is never committed to version control.

### 5.5 Where things are

| Folder | Contents |
|---|---|
| `apps/api` | The API and worker (Python). Prompts are in `src/intake/extract/prompts/` |
| `apps/web` | The web page (Next.js) |
| `data/seed` | The 120 demo invoices, answer keys (`truth/`), `master.json`, `MANIFEST.md` |
| `data/golden` | The fixed 60-invoice test set for accuracy. Never edited |
| `data/inbox` | Files staged for `make ingest-inbox` (not committed) |
| `docs` | This manual, the report, the playbook, architecture, runbook, decisions |

**Technical notes.** Stored files live under `storage/<tenant>/originals/` (named by SHA-256) and
`storage/<tenant>/pages/<document>/` (page PNGs and `text.json`). In Compose this is the `storage`
volume shared by the API and worker.

### 5.6 Why an invoice can be `failed`

The reason is in the invoice's history (`status_changed` and `extraction_failed`).

| Reason | Meaning | What to do |
|---|---|---|
| `UNREADABLE_DOCUMENT` | The page is blank; nothing was sent to the model | Ask the supplier for a clear copy |
| `TOO_MANY_PAGES`, `TOO_MANY_TOKENS` | Over the per-document budget; nothing was sent | Upload only the invoice pages |
| `SCHEMA_INVALID` | The model twice gave an answer that did not fit the required form | Try again later; report if it repeats |
| `JOB_FAILED:<Class>` | A background job used all its attempts; the class names the kind of error (for example `TransientLlmError` for a provider outage, `PermanentLlmError` for a rejected key) | Fix the cause, then re-queue (runbook) |

Other history entries: `extraction_completed` (model, prompt version, whether the answer was reused,
cost, and any doubtful critical fields) and `extraction_paused` (the limit was reached).

### 5.7 Why a value was left empty

Shown in `signals.normalize_error` of `field_extractions`. Confidence is 0 for all of these.

| Code | Meaning |
|---|---|
| `AMBIGUOUS_DATE` | Could be day-first or month-first (`03/04/2026`). Dotted dates (`07.06.2026`) are read day-first |
| `AMBIGUOUS_NUMBER` | Could be a thousands mark or a decimal mark (`1.200`, `1.234` as an amount) |
| `AMBIGUOUS_CURRENCY` | A bare `$` or `¥`, and the supplier's usual currency does not settle it |
| `UNSUPPORTED_CURRENCY` | A currency we do not handle yet (supported: USD, EUR, GBP, JPY) |
| `NO_CURRENCY` | An amount was printed but the currency is unknown, so the amount was not read |
| `UNPARSEABLE` | Not a date or number we can read (`about a thousand`, `1.234.56`, a currency that does not match) |

---

## 6. What the exceptions mean

Eighteen kinds of problem exist. **Detecting them starts in M4 and finishes in M7; the list is fixed
today and every one is planted in the demo data.** The exact wording comes from
[`exception-taxonomy.md`](exception-taxonomy.md).

| Code | Severity | In plain words | What a person does |
|---|---|---|---|
| `UNREADABLE_DOCUMENT` | block | The file opens but is too blurry or blank to read | Ask the supplier for a clearer copy |
| `LOW_CONFIDENCE_FIELD` | review | The system is unsure about a value, such as the total | Check the highlighted field; confirm or correct |
| `LINE_MATH_MISMATCH` | review | A line's quantity times price does not equal its amount | Confirm with the supplier; maybe a typo |
| `TOTAL_MISMATCH` | review | The lines add up to something different from the printed total | Ask for a corrected invoice |
| `TAX_MISMATCH` | review | The tax is not the expected share of the subtotal | Check the supplier's tax rate |
| `INVALID_DATE` | review | A date is impossible (far in the future, due before issued) | Confirm the date with the supplier |
| `UNKNOWN_SUPPLIER` | review | The supplier is not in the records; a close match may be shown | Link to an existing supplier or add a new one |
| `BANK_DETAILS_CHANGED` | block | The bank account differs from the one on file. A common fraud pattern | **Phone a known contact to verify before approving** |
| `POSSIBLE_DUPLICATE` | block | Looks like an invoice already received | Compare side by side; reject if duplicate |
| `NO_PO` | review | No purchase order number and none matches | Ask the requester, or approve as a non-PO invoice |
| `PO_NOT_FOUND` | review | The purchase order number is not in the system | Check for a typo or ask purchasing |
| `PRICE_VARIANCE` | review | A unit price is higher than the purchase order allows | Accept it, or ask the supplier to correct |
| `QTY_VARIANCE` | review | The invoice bills more units than ordered | Ask the supplier or purchasing |
| `RECEIPT_MISSING` | review | Nothing has been recorded as delivered | Wait for the receipt or confirm delivery |
| `QTY_NOT_RECEIVED` | review | Billed for more than has been received | Hold, or pay partially when the rest arrives |
| `PO_OVERBILLED` | block | Invoices against a purchase order now exceed its total | Stop and check with purchasing |
| `CURRENCY_MISMATCH` | review | The invoice and the purchase order use different currencies | Confirm with the supplier |
| `ABOVE_APPROVAL_LIMIT` | review | The total is above the automatic-approval limit | Needs manager approval |

---

## 7. What is coming

| When | You will be able to |
|---|---|
| M4 to M7 | See every problem explained with real numbers and a suggested fix, and each invoice routed |
| M8 | Work the review queue in a browser, on a phone too; correct fields; approve or reject |
| M9 | Read the complete history of any invoice |
| M10 | See measured accuracy per field and per document quality |
| M11 | See a dashboard of volume, exceptions, cost and estimated savings |
| M12 | Export approved invoices to CSV or JSON |
| M13 | Run the whole demo from a clean reset |

---

## 8. Safety and data, in plain words

- **Made-up data only.** All documents in this project are synthetic.
- **Bank details are protected.** Stored encrypted; the system compares accounts using a one-way
  fingerprint; screens will show only the last four digits.
- **Files are stored under their fingerprint, not their name,** so a hostile file name cannot reach
  other folders. The file's real type is decided by its contents.
- **Nothing is public** except the health check. Everything else needs the token.
- **Nothing pays or transfers money.** There is no code that can.
- **The history cannot be tampered with.** The database itself blocks edits and deletes.
- **Limits protect the system** from oversized or booby-trapped files.
- **The model's answer is checked, not trusted.** It must fit a strict form, is validated again, and
  nothing it says can change a status or a decision. Text inside an invoice that tries to give
  instructions is ignored.
- **Bank details stay protected** in the extracted fields and in the saved model answers.
- **Spending has a daily limit** and every model call is logged with its cost.
- **What the model provider sees** and how long they keep it: [`data-handling.md`](data-handling.md).

**Before using real client data,** read the open items in the report (section 7): sandboxing, container
hardening, the database password and encryption of stored files.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Web page says "API: unreachable" | API not running or wrong port | `docker compose ps`; check `API_PORT` |
| `401` from the API | Missing or wrong token | Use the value of `API_TOKEN` in `.env` |
| `503` from the API | No `API_TOKEN` configured | Run `make dev` or `make seed`, which generate one |
| `make dev` fails: port in use | Another program has the port | Set another port in `.env` |
| Upload gives `201` but `doc_quality` stays `unknown` | Worker has not run yet | Wait a few seconds; check `/jobs` and the worker logs |
| `make ingest-inbox` says `data/inbox is missing` | Demo not seeded | Run `make seed` first |
| Same file "not created" | It was already uploaded (`200`, `duplicate: true`) | Expected behaviour |
| Invoices stay `received` and `/jobs` shows `paused` | Reading is not configured, or the daily limit is reached | `GET /extraction/status`; set the key (section 4.6) or raise the limit (4.7) |
| `extraction_paused` in an invoice's history | The daily limit was reached | It resumes at 00:00 UTC, or raise `DAILY_SPEND_CAP_USD` |
| Invoice is `failed` | See the reason in section 5.6 | Fix the cause, then re-queue (runbook) |
| Currency and amounts are empty for an invoice | A bare `$` the supplier's usual currency does not settle, or another reason in section 5.7 | Expected: a person confirms it (M8) |
| Almost every scanned or photo field is under 80% | No text layer to check against | Expected: they need supporting evidence, or a person |

More on operations: [`runbook.md`](runbook.md). Design details: [`architecture.md`](architecture.md).

---

## 10. Glossary

| Term | Meaning |
|---|---|
| **Accounts payable (AP)** | The team that checks and pays supplier invoices |
| **API** | The front door programs use to talk to the system |
| **Audit log** | The permanent, tamper-proof history of every action |
| **Cache (saved answer)** | The model's answer for a file, kept so the same file is never paid for twice |
| **Confidence** | How sure the system is about a value it read |
| **Daily spend cap** | The most the system may spend on model calls in one UTC day; reading pauses when it is reached |
| **Duplicate** | The same invoice received more than once |
| **Exception** | A problem the system found, with an explanation and a suggested fix |
| **Extraction** | Reading an invoice into fields: supplier, dates, amounts, lines |
| **False clear** | An invoice wrongly passed as fine. The number we most want at zero |
| **Golden set** | 60 documents kept fixed to measure accuracy honestly over time |
| **Goods receipt** | Record that ordered goods actually arrived |
| **Job / queue** | A piece of background work waiting for the worker |
| **Minor units** | Money as whole cents (and a currency code), never decimals |
| **PO (purchase order)** | The company's order to the supplier |
| **SHA-256 fingerprint** | A short code that identifies a file's exact contents |
| **Synthetic data** | Made-up documents that look real |
| **Tenant** | One client's separate slice of the system |
| **Text layer** | The text already inside a digital PDF, used to double-check what was read from the picture |
| **Three-way match** | Comparing the invoice, the purchase order and the goods receipt |
| **Touchless rate** | Share of invoices cleared with no human work |
| **Worker** | The background program that processes jobs |

---

## 11. How this manual is kept up to date

Every milestone pull request updates this manual for whatever a user or operator can now do:
new guides in section 4, new rows in the reference tables, items moved from **[Coming in M#]** to
available, and new troubleshooting entries. It is updated together with [`report.md`](report.md).
