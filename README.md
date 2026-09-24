# Invoice Intake Agent

BeyondAI Demo A: reads supplier invoices, checks them against purchase orders and receipts,
and routes anything uncertain to a human. It never pays or moves money.

The full spec is in [`docs/playbook.md`](docs/playbook.md). Working rules for contributors
(and Claude Code) are in [`CLAUDE.md`](CLAUDE.md).

**Start here, whoever you are:**
- [`docs/manual.md`](docs/manual.md): how to run and use it (plain words, steps, technical notes).
- [`docs/report.md`](docs/report.md): what has been built, why, how it works, and what was proved,
  milestone by milestone. Both are updated in every milestone PR.

## Run it

Requirements: Docker with Compose. For local development also `uv`, Node 24 and `pnpm`.

```bash
cp .env.example .env      # optional: defaults work without it
docker compose up --build # or: make dev
```

Open http://localhost:3000. You should see **API: healthy**.
The API is at http://localhost:8000 (docs at `/docs`).

**Port already in use?** Set `API_PORT`, `WEB_PORT` or `DB_PORT` in `.env` and re-run.
The web page reaches the API over the Compose network, so changing `API_PORT` only affects the host.

## Develop

| Command | What it does |
|---|---|
| `make dev` | start db, api, worker, web |
| `make check` | lint + types + tests for api and web |
| `make gen-api` | regenerate web API types from the running API's OpenAPI schema |
| `make seed` | load demo master data, stage 120 invoice files in `data/inbox/` (safe to re-run) |
| `make ingest-inbox` | feed `data/inbox/` through ingestion (worker processes it) |
| `make generate` | rebuild the synthetic dataset in `data/seed/` (deterministic) |
| `make eval` / `make demo-reset` | stubs until M10 / M13 |

Upload a document (token is in `.env`, generated on first `make dev`/`make seed`):

```bash
curl -H "Authorization: Bearer $(grep '^API_TOKEN=' .env | cut -d= -f2-)" \
     -F "file=@some-invoice.pdf" http://localhost:8000/documents
```
See [`docs/runbook.md`](docs/runbook.md) for jobs, limits and troubleshooting.

Layout: `apps/api` (FastAPI + worker, pure decision logic in `src/intake/core/`),
`apps/web` (Next.js), `data/`, `eval/`, `docs/` (ADRs in `docs/decisions/`).

Run the API without Docker: `cd apps/api && uv sync && uv run uvicorn intake.main:app --reload`
(needs a Postgres reachable at `DATABASE_URL`; migrations: `uv run alembic upgrade head`).
