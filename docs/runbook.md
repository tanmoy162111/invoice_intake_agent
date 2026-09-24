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

## Limits worth knowing
- Uploads are capped by `MAX_UPLOAD_BYTES` (15 MB) and `MAX_PAGES` (10). The API refuses oversized
  files after reading at most limit+1 bytes, but the web server still receives the request body first.
  In any shared deployment put a request-size limit on the reverse proxy in front of the API.
- The audit log (`audit_events`) is append-only; never try to edit it.
