# Runbook

## Start, stop, reset
- `make dev` starts db, api, worker and web (creates `.env` and generated secrets if missing).
- `make seed` loads demo master data and stages 120 invoice files in `data/inbox/`.
- `make ingest-inbox` pushes those files through ingestion; the worker then processes them.
- Port clash? Set `API_PORT`, `WEB_PORT`, `DB_PORT` in `.env`. Postgres is published on
  `127.0.0.1` only; the api and worker reach it over the Compose network.
- `make gen-api` regenerates the web API types (needs the API running and `API_TOKEN` in `.env`).

## Upload a file
```bash
TOKEN=$(grep '^API_TOKEN=' .env | cut -d= -f2-)
curl -H "Authorization: Bearer $TOKEN" -F "file=@invoice.pdf" http://localhost:8000/documents
```
`201` new document, `200` the same file was already uploaded, `413/415/422` rejected (the body
says why and how to fix it).

## Jobs look stuck or failed
`GET /jobs` (same bearer token):
- `counts.queued` growing and `oldest_queued_age_s` large: the worker is down or slow. Check
  `docker compose logs worker` and restart it (`docker compose restart worker`).
- `stuck > 0`: a job has been `running` longer than `JOB_VISIBILITY_TIMEOUT_S`. The worker's reaper
  re-queues it automatically on its next loop; if the worker is not running, start it.
- `failed` lists jobs that used all `JOB_MAX_ATTEMPTS` with the exception class of the last error
  (messages are deliberately not stored). For detail, set `LOG_LEVEL=DEBUG` on the worker and re-run
  the job, then read `docker compose logs worker`. DEBUG logs can contain document content: use
  synthetic data only, and turn it off afterwards. Fix the cause, then
  re-queue: `update jobs set status='queued', attempts=0, run_after=now(), last_error=null where id='...'`.
  Each retry and final failure also writes `job_retry_scheduled` / `job_failed` to the audit log.

## Extraction: paused, capped or failed
`GET /extraction/status` (same bearer token) is the first stop.
- `configured: false`: set `ANTHROPIC_API_KEY` (or `LLM_PROVIDER=ollama` with a model), `EXTRACTION_MODEL`
  and `BANK_ENCRYPTION_KEY` in `.env`, then `docker compose restart worker api`. Waiting jobs resume by
  themselves (`last_error = EXTRACTION_NOT_CONFIGURED`, retried every `EXTRACT_NOT_CONFIGURED_RETRY_S`).
- `cap_reached: true`: spend hit `DAILY_SPEND_CAP_USD`. Jobs wait with `last_error = SPEND_CAP_REACHED`
  until 00:00 UTC. To resume sooner, raise the cap, restart the worker and pull the jobs forward:
  `update jobs set run_after=now() where last_error='SPEND_CAP_REACHED' and status='queued'`.
- An invoice `failed` with `JOB_FAILED:PermanentLlmError`: the provider rejected the key or request
  (check the key, model name and account access). `JOB_FAILED:TransientLlmError`: an outage or rate limit
  outlasted the retries. After fixing, re-queue the job as above; the extract step accepts an invoice in
  `failed` and reuses any saved answer, so nothing is paid for twice.
- Every model call is a row in `llm_calls` (status `ok`, `schema_invalid`, `output_invalid`,
  `transient_error`, `rejected`). Compare `sum(cost_usd_micros)` for today with the cap to audit spend.
- `llm_calls.response` holds supplier data in the clear (bank account sealed). Purge or scrub it before
  sharing a database.

## Validation: what the checks say
- `select check_code, details->>'outcome', details from check_results where invoice_id='...'` shows every
  check with its numbers. `skipped` means the check could not be done; never read it as a pass.
- A `validate_invoice` job that fails leaves the invoice in `checking` with a failed job in `/jobs`.
  Fix the cause and re-queue it (same `update jobs ...` as above). Only an `extracted` invoice is
  validated, so to re-run one that is already `checking`, delete its `check_results` rows and set its
  status back to `extracted` (this writes no audit event, so note it in the ticket).
- `checks_completed` in the invoice history records `as_of_overridden: true` when `VALIDATION_TODAY` was in
  effect. It must be empty in production (the API refuses to start with `APP_ENV=production` and it set).

## Duplicate check
- `POSSIBLE_DUPLICATE` rows are in `check_results` (see the manual, section 4.12). `fail` names the earlier
  invoice in `details.existing_invoice_id`; compare the two documents side by side.
- Jobs of type `detect_duplicates` with `last_error = WAITING_FOR_EARLIER_INVOICES` are waiting for an earlier
  invoice that is still `received`, `extracting` or `extracted`. If it never moves (extraction paused or
  failed), fix that first; the check gives up waiting after `DEDUPE_MAX_WAIT_S` and records `skipped`.
- To re-run the check for one invoice, delete its `POSSIBLE_DUPLICATE` row and re-queue its job
  (`update jobs set status='queued', attempts=0, run_after=now() where id='...'`); the invoice must still be
  `checking`. Note the action in the ticket (a manual delete of a check row writes no audit event).

## 3-way match
- The seven match rows are in `check_results` (manual, section 4.13). `PO_OVERBILLED.details` holds the running
  total; `skipped` rows carry a `reason`, and none of them is a pass.
- Jobs of type `match_invoice` with `last_error = WAITING_FOR_EARLIER_INVOICES` are waiting for an earlier
  invoice that is unread, or still `checking` without match rows. Fix the stuck earlier invoice first (usually a
  paused or failed extraction, or a duplicate-check job that has not run); the match gives up waiting after
  `MATCH_MAX_WAIT_S` and records `skipped` for the checks that depend on earlier billing.
- `EARLIER_BILLING_UNKNOWN` on many invoices of one PO means an earlier invoice on it has an unreadable quantity
  or amount. Correct that invoice (M8) or check it by hand; later invoices on the PO stay "could not check".
- To re-run the match for one invoice, delete its seven match rows and re-queue its `match_invoice` job as above;
  the invoice must still be `checking`. Later invoices on the same PO were matched with the old numbers, so
  re-run them too, in received order. Note the action in the ticket (a manual delete writes no audit event).

## Exceptions and routing
- `exceptions` rows and the route are in the database (manual, section 4.14). The reasons an invoice went to
  review are on its `status_changed` and `routing_decided` history events.
- Jobs of type `route_invoice` run right after `match_invoice`. An invoice that stays `checking` after its match
  rows exist means the route job did not run: check `/jobs`.
- Do not edit `exceptions`, `route` or `status` by hand to "fix" a routing decision: it would leave the history
  disagreeing with the data. Correcting a field and re-running the checks arrives in M8.
- `MISSING_CHECKS` on an invoice means a check result is absent (an earlier stage did not finish or a row was
  deleted). Re-run the missing stage's job before trusting the route.
- A failed invoice with an `UNREADABLE_DOCUMENT` exception is a blank document; ask the supplier for a new copy.

## Reviewer login and actions
- Login needs `REVIEWER_PASSWORD` and `SESSION_SECRET` in `.env` (`make dev` generates both). Changing
  `SESSION_SECRET` signs every reviewer out. Sessions last `SESSION_TTL_S` (8 hours by default).
- Login attempts are limited to 5 a minute per client address and 50 a minute overall (per API process; a restart
  clears it). A locked login answers 429 with `Retry-After`. The client address is the connection's: a
  `X-Forwarded-For` header is not trusted, so behind a proxy the proxy must present the real address.
- Every sign-in and every failed sign-in is an audit event (`login_succeeded`, `login_failed`; the name typed on a
  failure is not recorded). A burst of `login_failed` is a password-guessing attempt.
- A session ends when `REVIEWER_PASSWORD` is emptied or `REVIEWER_USERNAME` changes, as well as when it expires
  or `SESSION_SECRET` changes. There is no per-session revocation.
- Every reviewer action is a `review_actions` row and an audit event. To see what a reviewer did to an invoice:
  `select created_at, user_id, action, payload from review_actions where invoice_id = '<id>' order by created_at;`
- A correction re-checks only that invoice. Invoices matched to the same purchase order *after* it keep their old
  cumulative-billing result; correct or re-check them in received order.
- Do not edit `exceptions`, `check_results` or `invoices.status` by hand after a reviewer has acted: the history
  and the data would disagree. Approve and reject are final.

## The web app
- Compose publishes the web and API ports on `127.0.0.1` only. The demo cookie is not `Secure` (plain http), so
  the site should not be reachable from a network. To serve others, put an https reverse proxy in front, remove
  `COOKIE_INSECURE`, set `ALLOWED_ORIGINS` if it rewrites the Host header, set `TRUST_FORWARDED_FOR=1` on the web
  service and `TRUSTED_PROXIES` (the web server's network) on the API, so each visitor is throttled separately.
- The login throttle keys on the address the API sees. Without `TRUSTED_PROXIES` that is the web server for
  everyone, so one person's failed attempts lock the login for all for a minute (fifty attempts a minute overall).
- Signing out only clears the browser's cookie. The token stays valid until `SESSION_TTL_S` passes; to end
  all sessions at once, change `SESSION_SECRET`.
- The web server needs `API_URL` (Compose sets it) and, on plain http, `COOKIE_INSECURE=1` (Compose sets it) so the
  session cookie is accepted; behind https leave it unset.
- Signing in repeatedly fails with "not set up": the API has no `REVIEWER_PASSWORD` or `SESSION_SECRET`.
- Sent back to sign-in on every page: the session cookie is missing (a Secure cookie on http; set
  `COOKIE_INSECURE=1`), the session ended, or the API returned 401 (changed secret, name or password).
- A page image is blank: the document's pages are rendered by the worker after upload; check `/jobs`.
- After changing the API, run `make gen-api` and commit `apps/web/src/lib/api/`; the API test suite fails otherwise.
- To look at the screens with realistic data without a model: create a scratch database, migrate it, then
  `DATABASE_URL=... STORAGE_DIR=... make demo-pipeline` (uses recorded answers; never calls a model) and start the
  API with the same values and `BANK_ENCRYPTION_KEY` from the script's output and `VALIDATION_TODAY=2026-09-25`.

## Limits worth knowing
- Uploads are capped by `MAX_UPLOAD_BYTES` (15 MB) and `MAX_PAGES` (10). The API refuses oversized
  files after reading at most limit+1 bytes, but the web server still receives the request body first.
  In any shared deployment put a request-size limit on the reverse proxy in front of the API.
- The audit log (`audit_events`) is append-only; never try to edit it.
