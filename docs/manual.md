# Invoice Intake Agent: Manual

> **Living document.** Updated in every milestone pull request, as new abilities appear.
> **Describes:** the system after M8a (the review API: login, queue, corrections, decisions), 2026-09-26.
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
| **Reviewer** (AP clerk) | Works the queue of invoices that need a person; corrects, approves, rejects | Through the API today (section 4.15); the browser screen is [Coming in M8b] |
| **Approver / manager** | Approves invoices above the limit | [Coming in M8] |
| **Operator** (IT or support) | Starts the system, loads data, watches the background jobs | Today |
| **Developer** | Builds and tests the system | Today |

**Today, the only way in is the API** (a set of web addresses a program or the `curl` command can call).
The reviewer screen arrives in M8b; the actions it will use already exist in the API (section 4.15). The web page you can open now only shows "API: healthy".

---

## 2. Ideas you need

### An invoice's life

Every invoice moves through these steps. Each change is written to a permanent history.

| Status | Plain meaning | Reached by |
|---|---|---|
| `received` | We have the file and it is waiting to be read | Today (M2) |
| `extracting` / `extracted` | Fields are being read / have been read | Today (M3) |
| `checking` | The checks have run and their results are saved; the router decides next (a few seconds) | Today (M4 to M6) |
| `cleared` | Nothing is in doubt: no open exception, every important field confident, every check ran, total within the approval limit. Waits for a person to approve | Today (M7) |
| `needs_review` | A person must look. The reasons are in the history (section 4.14) | Today (M7) |
| `approved` / `rejected` | A person decided (section 4.15). Both are final; a correction or a re-check is no longer possible | Today (M8a, through the API) |
| `exported` | Handed to the accounting system | [M12] |
| `failed` | Reading failed, with a reason (section 5.6); can be retried | Today (M3) |

An invoice above the approval limit always needs a person, even if every check passed. A blank document
never reaches the checks: it becomes `failed` and shows an `UNREADABLE_DOCUMENT` exception (section 4.14).

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

There is no reviewer screen yet (M8b). To look at one invoice, use the API (section 4.15) or ask the database:

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

### 4.10 See the checks on an invoice

After reading, every invoice is checked seven ways. Each check ends as `pass`, `fail` or `skipped`
(*could not check*: a value was missing or unreadable). **`skipped` is never a pass.**

```bash
docker compose exec db psql -U intake -c "
  select check_code, details->>'outcome' as outcome, details
  from check_results where invoice_id = '<invoice-id>' order by check_code;"
```

| Check | Fails when | Useful details |
|---|---|---|
| `LINE_MATH_MISMATCH` | quantity times price differs from a line amount by more than a cent | the lines, with expected and actual amounts |
| `TOTAL_MISMATCH` | lines do not add up to the subtotal, or subtotal plus tax is not the total | each comparison, expected and actual |
| `TAX_MISMATCH` | the tax differs from the supplier's usual rate (or the line rates) | expected and actual tax, the rate and its source |
| `INVALID_DATE` | invoice date in the future or too old, or due date before the invoice date | `reasons`: `FUTURE_INVOICE_DATE`, `INVOICE_TOO_OLD`, `DUE_BEFORE_INVOICE` |
| `CURRENCY_MISMATCH` | currency differs from the supplier's usual currency | both currencies and what was printed |
| `UNKNOWN_SUPPLIER` | no exact tax ID or name/alias match, or the identifiers conflict | the closest known supplier and its similarity; `conflict` |
| `BANK_DETAILS_CHANGED` | the account differs from the one on file | never shows the account |

Turning failures into explained exceptions and routing the invoice is the last step (section 4.14). The supplier is linked on the invoice (`invoices.supplier_id`) when it is
known. `checks_completed` in the invoice's history lists which checks failed or were skipped.

`UNKNOWN_SUPPLIER` details use `conflict` values: `NAME_DIFFERS_FROM_TAX_ID` (the tax ID belongs to a
different supplier than the name) and `TAX_ID_DIFFERS_FROM_RECORD` (a known name with another tax ID).
`skipped` bank checks say why in `reason` (`BANK_NOT_READ`, `NO_BANK_ON_FILE`, `UNKNOWN_SUPPLIER`).

### 4.11 Adjust the check rules for a client

The limits live in the client's settings (`tenants.settings`). Change one and new checks use it:

```bash
docker compose exec db psql -U intake -c "
  update tenants set settings = settings || '{\"max_invoice_age_days\": 90}'::jsonb;"
```

| Setting | Default | Meaning |
|---|---|---|
| `max_invoice_age_days` | 365 | Older invoices fail `INVALID_DATE` |
| `future_date_tolerance_days` | 0 | How many days ahead an invoice date may be |
| `line_tolerance_minor` | 1 | Rounding allowed per line, in cents |
| `total_tolerance_minor` | 1 | Rounding allowed between subtotal plus tax and the total |
| `tax_tolerance_per_line_minor` | 1 | Rounding allowed on tax (per line when line rates are used) |
| `approval_amount_limit_minor` | none | The largest total, in cents, that may clear without a person, for invoices in `approval_limit_currency` |
| `approval_limit_currency` | `USD` | Which currency `approval_amount_limit_minor` is in |
| `approval_amount_limits_minor` | none | Limits for other currencies, for example `{"EUR": 1000000, "GBP": 1000000}`. An invoice in a currency with no limit always needs review |
| `supplier_fuzzy_min` | 90 | A closest supplier at or above this similarity is *suggested* (never trusted) |
| `dedupe_window_days` | 7 | Days apart two invoices can be and still be a soft duplicate |
| `dedupe_number_similarity_min` | 85 | How similar two invoice numbers must be (0 to 100) for a soft duplicate |
| `match_price_tolerance_bp` | 200 | How far a unit price may differ from the purchase order, either way, in basis points (200 = 2%) |
| `match_qty_tolerance_bp` | 0 | How far the billed quantity may exceed the ordered quantity, in basis points (0 = exact) |
| `match_description_similarity_min` | 80 | How alike two line descriptions must be (0 to 100) to count as the same line when there is no SKU |
| `match_po_total_tolerance_bp` | 200 | When an invoice names no purchase order: how close the subtotal must be to an open order's total for that order to be suggested |
| `match_overbill_tolerance_minor` | 0 | Extra billing above a purchase order's total that is still accepted, in cents |

An invalid value is ignored and the default is used. Old invoices in the demo data will start to look
"too old" a year after they are dated; set `VALIDATION_TODAY` (section 5.4) to keep a demo stable.

### 4.12 See whether an invoice is a duplicate

After the checks, each invoice is compared with what the same supplier sent earlier. The answer is one more
row in `check_results`, with the check code `POSSIBLE_DUPLICATE`:

```bash
docker compose exec db psql -U intake -c "
  select details->>'outcome' as outcome, details
  from check_results
  where invoice_id = '<invoice-id>' and check_code = 'POSSIBLE_DUPLICATE';"
```

| Outcome | Meaning |
|---|---|
| `pass` | No earlier invoice from this supplier matches |
| `fail` | A match. `details.kind` is `hard` (same supplier and the same number, ignoring case, spaces, dashes and leading zeros) or `soft` (same total, currency and a date within the window, with a very similar number). `existing_invoice_id`, `existing_invoice_number`, `existing_invoice_date`, `similarity` and `days_apart` say which invoice and how close |
| `skipped` | Could not check: `NO_SUPPLIER`, `NO_INVOICE_NUMBER`, `INCOMPLETE_FOR_SOFT_MATCH` (a similar earlier invoice, or this one, is missing its total, date or currency, or the total is zero), `SUPPLIER_UNCERTAIN` (a similar number from an invoice whose supplier is linked on one side and only printed on the other, under a name that could be the same company written another way), `EARLIER_INVOICES_STILL_PENDING` (the wait ran out) or `TOO_MANY_TO_COMPARE` (more than 2,000 earlier invoices to compare; refused rather than cut short). **Never read `skipped` as a pass** |

Only the later invoice is flagged; the earlier one is never marked. While earlier invoices are still being
read the check waits: the job shows under `paused` in `/jobs` with `last_error` `WAITING_FOR_EARLIER_INVOICES`.
It checks again every 10 seconds and goes ahead after 5 minutes (`DEDUPE_POLL_S`, `DEDUPE_MAX_WAIT_S`).
Two client settings tune it (section 4.11): `dedupe_window_days` (7) and `dedupe_number_similarity_min` (85).
A file that is byte-for-byte the same is refused earlier, at upload (section 5.1).

### 4.13 See the 3-way match with the purchase order

After the duplicate check, each invoice is compared with its purchase order (PO) and the goods receipts
recorded for it. Seven more rows appear in `check_results`, one per code, always all seven:

```bash
docker compose exec db psql -U intake -c "
  select check_code, details->>'outcome' as outcome, details
  from check_results
  where invoice_id = '<invoice-id>'
    and check_code in ('NO_PO','PO_NOT_FOUND','PRICE_VARIANCE','QTY_VARIANCE',
                       'RECEIPT_MISSING','QTY_NOT_RECEIVED','PO_OVERBILLED');"
```

| Code | `fail` means |
|---|---|
| `NO_PO` | The invoice names no PO and no open PO of that supplier, in the same currency, has a total within tolerance of the subtotal. If exactly one does, it is used for the other checks but the result is `skipped` (`PO_INFERRED`, its number in `details`): a PO guessed from the total is for a person to confirm |
| `PO_NOT_FOUND` | The PO number is not in the system (`NOT_IN_SYSTEM`), belongs to a different supplier (`PO_OF_OTHER_SUPPLIER`), or the PO is not open, for example closed or cancelled (`PO_NOT_OPEN`). If the invoice's supplier is not known the result is `skipped` (`SUPPLIER_UNKNOWN`) |
| `PRICE_VARIANCE` | A unit price is further from the PO price than the limit. `findings` lists each line, both prices, the variance and the limit |
| `QTY_VARIANCE` | A line bills more than the PO ordered, counting what earlier invoices already billed (`OVER_PO_QTY`), or a line is not on the PO at all (`LINE_NOT_ON_PO`). Billing *less* than ordered is fine: it is a partial invoice |
| `RECEIPT_MISSING` | Nothing has been received for the PO |
| `QTY_NOT_RECEIVED` | A line bills more than has been received so far, across all receipts and earlier invoices |
| `PO_OVERBILLED` | This invoice's subtotal plus what earlier invoices billed against the PO is more than the PO total (both are before tax) |

Invoice lines are matched to PO lines by SKU first, then by similar description, then by amount. A PO line is
used once, and a line that could be either of two PO lines is left unmatched instead of guessed (it then
shows as `LINE_NOT_ON_PO`). A SKU shared by two PO lines does not pair by SKU, and a line whose SKU differs
from the PO line's SKU is never paired by description or amount (that would hide a substitution). "Earlier" means received earlier. Which PO lines each invoice line matched is
stored on the invoice line (`invoice_lines.matched_po_line_id`).

`skipped` means the check could not be done, and **must never be read as a pass**. Reasons: `NO_PO`
(no purchase order was found, so nothing else could be compared), `CANNOT_INFER_PO` (no PO number and the
supplier, currency or subtotal is missing), `PO_INFERRED` (see `NO_PO` above), `SUPPLIER_UNKNOWN`,
`AMBIGUOUS_PO` (no number and more than one open PO fits, or two POs of the supplier have the same number once
punctuation is ignored), `CREDIT_NOTE` (a negative quantity, amount or subtotal: a person decides, and it is
never counted as reducing what was billed),
`NO_LINES` and `PO_HAS_NO_LINES`, `NO_RECEIPT` (the receipt check is already `RECEIPT_MISSING`),
`NO_MATCHED_LINES`, `UNREADABLE_LINE` (a matched line has no readable price or quantity),
`CURRENCY_DIFFERS_FROM_PO` and `CURRENCY_UNKNOWN` (amounts are not compared across currencies),
`NO_SUBTOTAL`, `EARLIER_INVOICES_STILL_PENDING` (the wait ran out), `EARLIER_BILLING_UNKNOWN` (an earlier
invoice on the same PO has an unreadable quantity or amount, so the running total cannot be trusted) and
`TOO_MANY_POS_TO_COMPARE` (more than 2,000 purchase orders to search; refused rather than cut short).

While earlier invoices are still being read or matched the job waits: it shows under `paused` in `/jobs`
with `last_error` `WAITING_FOR_EARLIER_INVOICES`. It checks again every 10 seconds and goes ahead after
5 minutes (`MATCH_POLL_S`, `MATCH_MAX_WAIT_S`). Five client settings tune the matching (section 4.11).
The currency rule (`CURRENCY_MISMATCH`) is a validation check (section 4.10); the matching skips amount
checks when the currencies differ instead of repeating it.

### 4.14 See the exceptions and where the invoice went

The last step reads every check result, raises the exceptions and decides the route. Ask what it decided:

```bash
docker compose exec db psql -U intake -c "
  select i.status, i.route, e.code, e.severity, e.explanation, e.suggested_fix
  from invoices i left join exceptions e on e.invoice_id = i.id
  where i.id = '<invoice-id>' order by e.severity desc, e.code;"
```

Every failed check becomes **one** exception: a check that finds several problems (price on lines 2 and 4)
lists each line with its numbers in a single explanation. The explanation and the fix come from fixed
templates (section 6), filled with the numbers from the check, so they are always accurate. Three
exceptions have no check of their own: `LOW_CONFIDENCE_FIELD` (an important field is missing or below the
confidence minimum; each is named with its percentage), `ABOVE_APPROVAL_LIMIT`, and `UNREADABLE_DOCUMENT`.

**"Could not be checked."** A check that could not be done is never a pass. It is raised under its own code at
`review` severity, worded "The tax could not be checked (there is no tax rate on file...), so a person needs to
look at it." A check that was only skipped because of another problem is not repeated (a supplier that is not
known already explains why its currency and bank checks were skipped; a missing PO explains the skipped price
and quantity checks; a missing receipt explains the skipped received-quantity check).

**The route.** The invoice becomes `cleared` (route `straight_through`) only if all of these hold, otherwise
`needs_review` (route `review`):

- there is no open `review` or `block` exception;
- every important field (supplier, invoice number, date, total, currency) is at least as confident as
  `FIELD_CONFIDENCE_MIN`;
- the total is not above the client's limit for the invoice's currency (section 4.11), and is not zero or negative;
- every check has a result.

A changed bank account always sends the invoice to review, whatever its severity. The reasons are written to
the history: the status change carries them, and a `routing_decided` event lists them with the exceptions.
Reasons: `BANK_DETAILS_CHANGED`, `OPEN_EXCEPTIONS`, `LOW_CONFIDENCE`, `NO_TOTAL`, `CREDIT_NOTE`,
`ZERO_TOTAL`, `NO_LIMIT` (no approval limit is set for the invoice's currency), `ABOVE_LIMIT` and
`MISSING_CHECKS`. A bank check that merely could not be done (the account could not be read, or none is on
file) is a "could not be checked" exception, not a `BANK_DETAILS_CHANGED` reason.

Good to know:

- A limit is only ever compared with a total in the same currency; there is no exchange-rate conversion. An
  invoice in a currency with no configured limit always goes to review.
- Text copied from the document into an explanation (supplier and PO names, invoice numbers) is cleaned of
  control characters and cut to 80 characters (numbers to 40). It is plain text; a screen showing it must not
  treat it as markup.
- A cleared invoice still waits for a person to approve it (M8); nothing is paid or moved.
- A blank document fails at reading (section 5.6). It stays `failed` (it can only be retried) and carries an
  open `UNREADABLE_DOCUMENT` exception so a reviewer sees it.
- Every exception, and the routing decision, is written to the history with codes only, never invoice text.

### 4.15 Review an invoice through the API

The browser screen is M8b. Everything it will do already works over the API, so a script or a tool such as
`curl` can do it today. First sign in (the password is `REVIEWER_PASSWORD` in `.env`):

```bash
TOKEN=$(curl -s localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"reviewer","password":"<REVIEWER_PASSWORD>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
AUTH="Authorization: Bearer $TOKEN"
curl -s -H "$AUTH" 'localhost:8000/invoices?limit=10'            # the queue, worst first
curl -s -H "$AUTH" localhost:8000/invoices/<invoice-id>            # one invoice in full
```

**The queue** (`GET /invoices`) lists `needs_review` and `failed` invoices (a blank document is `failed` with an
`UNREADABLE_DOCUMENT` exception). Repeat `status=` to change that, add `code=` to keep invoices with that
exception open, `supplier_id=` for one supplier, and `limit=` / `offset=` to page. Worst open exception first,
then oldest.

**One invoice** (`GET /invoices/<id>`) returns every field with its confidence and, for a doubtful one, the
reason in plain words ("nothing confirms it: no text layer, rule or supplier record supports it"), the lines,
the exceptions with their explanation and fix, the checks, why it was routed, and whether it can be approved.
A duplicate exception carries the other invoice for a side-by-side view. The bank account is only ever shown
masked (`••••••••6016`). Pages of the document are at `/invoices/<id>/pages/1`.

**Actions** all return the invoice as it now stands:

| Action | How | Rules |
|---|---|---|
| Correct a field | `POST /invoices/<id>/corrections` `{"field":"total","value":"1,234.50"}` | Header fields only (not the bank account, not lines yet). Amounts are read in the invoice currency, dates as `2026-05-31`. Optional fields (PO number, due date, payment terms, tax ID) may be emptied. The checks and routing run again at once; exceptions that no longer apply are closed by the system, and one a person already closed, unchanged, stays closed |
| Close an exception | `POST /exceptions/<id>/close` `{"resolution":"resolved","note":"..."}` | `resolved` (dealt with) or `dismissed` (not a problem). A `block` exception needs a note |
| Approve | `POST /invoices/<id>/approve` | Only when every exception is closed, and only from `cleared` or `needs_review`. Final |
| Reject | `POST /invoices/<id>/reject` `{"reason":"..."}` | A reason is required. Final |
| Request information | `POST /invoices/<id>/request-info` `{"note":"..."}` | Logged only; the status does not change |
| Reveal the bank account | `POST /invoices/<id>/bank/reveal` | Returns the account; the reveal is written to the history (without the account) |

A refused action answers 409 (the invoice is in the wrong state, or exceptions are still open) or 422 (a value
or note is missing or unreadable) with a `detail.code` such as `OPEN_EXCEPTIONS`, `NOTE_REQUIRED`,
`INVALID_VALUE`, `UNKNOWN_FIELD` or `WRONG_STATUS`. Actions need a reviewer session; the static API token can
read but not act. Every action is a row in `review_actions` and an audit event (field names and codes only,
never values).

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
| `POST /auth/login` | The reviewer signs in; returns a session token | No (the one route besides `/health`) |
| `GET /auth/me` | Who the token belongs to | Yes |
| `GET /invoices` | The queue: invoices that need a person, worst exception first | Yes |
| `GET /invoices/{invoice_id}` | One invoice: fields with confidence, lines, exceptions, checks, masked bank | Yes |
| `GET /invoices/{invoice_id}/pages/{page}` | A page of the document as a PNG | Yes |
| `POST /invoices/{invoice_id}/corrections` | Correct a field; the checks run again | Reviewer session |
| `POST /exceptions/{exception_id}/close` | Resolve or dismiss an exception | Reviewer session |
| `POST /invoices/{invoice_id}/approve` | Approve (every exception must be closed) | Reviewer session |
| `POST /invoices/{invoice_id}/reject` | Reject, with a reason | Reviewer session |
| `POST /invoices/{invoice_id}/request-info` | Record that information was requested | Reviewer session |
| `POST /invoices/{invoice_id}/bank/reveal` | Show the bank account (logged) | Reviewer session |
| `GET /openapi.json` | Machine-readable description of the API | Yes |

Send the token as `Authorization: Bearer <token>`. Two kinds are accepted: the static `API_TOKEN` (for
scripts: it can upload, read the queue and read invoices) and a reviewer's session token from
`/auth/login` (needed for every action, so the history names who acted). If neither is configured the server
refuses everything except `/health` and the login.

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
| `APP_ENV` | `development` | `development`, `test` or `production`. Production refuses `VALIDATION_TODAY` |
| `VALIDATION_TODAY` | empty | A fixed "as of" date (`YYYY-MM-DD`) for the date checks. Test and demo only |
| `DEDUPE_POLL_S`, `DEDUPE_MAX_WAIT_S` | 10, 300 | How often the duplicate check re-tries while earlier invoices are unread, and when it stops waiting (seconds) |
| `REVIEWER_USERNAME`, `REVIEWER_PASSWORD` | `reviewer`, empty | The one demo reviewer. An empty password switches login off. `make dev` generates a password into `.env` |
| `SESSION_SECRET`, `SESSION_TTL_S` | empty, 28800 | The key that signs reviewer sessions, and how long one lasts (seconds, at least 60). `make dev` generates the secret |
| `MATCH_POLL_S`, `MATCH_MAX_WAIT_S` | 10, 300 | The same, for the 3-way match: how often it re-tries while earlier invoices are unread or unmatched, and when it stops waiting (seconds) |
| `EXTRACT_TIMEOUT_S` | 120 | How long one model call may take before it counts as a failure to retry |
| `EXTRACT_NOT_CONFIGURED_RETRY_S` | 300 | How often a paused job checks whether reading has been configured |
| `FIELD_CONFIDENCE_MIN` | 0.8 | Below this a critical field counts as doubtful |
| `OLLAMA_BASE_URL`, `OLLAMA_NUM_CTX` | `http://localhost:11434`, 8192 | Local model address and context size |

`.env` holds secrets and is never committed to version control.

### 5.5 Where things are

| Folder | Contents |
|---|---|
| `apps/api` | The API and worker (Python). Prompts are in `src/intake/extract/prompts/`; the checks are `src/intake/core/validate.py` and `src/intake/checks/` |
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

Eighteen kinds of problem exist. **All of them are now raised by the system, each with an explanation that
states the real numbers, and every one is planted in the demo data.** The exact wording comes from
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
| M8b | Work the review queue in a browser, on a phone too (the actions already exist in the API, section 4.15) |
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
- **Checks never pass on missing data.** A check that cannot be done says so, and later routing treats it as needing a person.
- **Repeats are caught before approval:** a later invoice that matches an earlier one is flagged with a pointer to the match; "could not check" never counts as a pass.
- **Look-alike suppliers are not trusted:** a supplier is known only by an exact tax ID or exact name, and the two must agree.
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
| Duplicate check keeps waiting (`WAITING_FOR_EARLIER_INVOICES`) | An earlier invoice is still being read (or its reading is paused) | It goes ahead by itself after 5 minutes; fix the paused reading (section 4.6) |
| Two similar invoices, only one `POSSIBLE_DUPLICATE` | Only the later-received one is flagged | Expected (section 4.12) |
| `POST /auth/login` says 503 | No `REVIEWER_PASSWORD` or `SESSION_SECRET` is set | Set both in `.env` (`make dev` does), restart the API |
| `POST /auth/login` says 429 | Five wrong passwords in a minute lock the login for that minute | Wait a minute |
| A review action says 401 after a while | The session lasted `SESSION_TTL_S` | Sign in again |
| A review action says 403 | It was sent with the static API token, which cannot act | Use a session token from `/auth/login` |
| An invoice stays `checking` | The routing job has not run: an earlier stage is waiting, or the worker is stopped | Look at `/jobs` for `route_invoice`, `match_invoice` and `detect_duplicates` (section 4.4) |
| Almost every scanned or photographed invoice goes to review | Its invoice number has no text layer to confirm it, so confidence is 75%, below the 80% minimum | Expected with the current confidence rules; measured in M10, and a reviewer confirms the field (M8) |
| Every check is `skipped` | The invoice was read with empty fields (see section 5.7) | Fix the cause; a person confirms the fields (M8) |
| Old demo invoices fail `INVALID_DATE` as `INVOICE_TOO_OLD` | The demo dates are more than a year old | Set `VALIDATION_TODAY` or raise `max_invoice_age_days` |
| A real supplier shows as `UNKNOWN_SUPPLIER` with a 90+ similarity | Names must match exactly (or a tax ID) | Add the printed name as an alias or link the supplier |
| Almost every scanned or photo field is under 80% | No text layer to check against | Expected: they need supporting evidence, or a person |

More on operations: [`runbook.md`](runbook.md). Design details: [`architecture.md`](architecture.md).

---

## 10. Glossary

| Term | Meaning |
|---|---|
| **Accounts payable (AP)** | The team that checks and pays supplier invoices |
| **API** | The front door programs use to talk to the system |
| **Audit log** | The permanent, tamper-proof history of every action |
| **As-of date** | The date the date checks treat as today; normally today, or `VALIDATION_TODAY` in tests and demos |
| **Cache (saved answer)** | The model's answer for a file, kept so the same file is never paid for twice |
| **Confidence** | How sure the system is about a value it read |
| **Daily spend cap** | The most the system may spend on model calls in one UTC day; reading pauses when it is reached |
| **Duplicate** | The same invoice received more than once. *Hard*: same supplier and number. *Soft*: same supplier, total and currency, close dates and a very similar number |
| **Check result** | One check's answer for an invoice: pass, fail or could not check (skipped), with the numbers behind it |
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
| **Skipped (could not check)** | A check that could not be done because a value was missing or unreadable. Never counts as a pass |
| **Text layer** | The text already inside a digital PDF, used to double-check what was read from the picture |
| **Three-way match** | Comparing the invoice, the purchase order and the goods receipt |
| **Touchless rate** | Share of invoices cleared with no human work |
| **Worker** | The background program that processes jobs |

---

## 11. How this manual is kept up to date

Every milestone pull request updates this manual for whatever a user or operator can now do:
new guides in section 4, new rows in the reference tables, items moved from **[Coming in M#]** to
available, and new troubleshooting entries. It is updated together with [`report.md`](report.md).
