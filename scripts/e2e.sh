#!/usr/bin/env bash
# Browser test (make e2e): starts a throwaway stack, runs the Playwright tests, and cleans up.
#
# The stack is the real API, the real worker and the real web app on a scratch database, with
# LLM_PROVIDER=recorded so no model is ever called. The seed invoices are run through the pipeline
# first; one is held back for the test to upload.
#
#   E2E_DATABASE_URL  use this (empty, migrated-by-us) Postgres instead of starting one with Docker
#   E2E_DB_PORT       host port for the Docker database (default 5544)
#   E2E_API_PORT / E2E_WEB_PORT   default 8011 / 3011
#   E2E_SKIP_BUILD=1  reuse an existing `pnpm build`
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD

DB_PORT=${E2E_DB_PORT:-5544}
API_PORT=${E2E_API_PORT:-8011}
WEB_PORT=${E2E_WEB_PORT:-3011}
UPLOAD_FILE=Brightline-BL-2026-0540.pdf
WORK=$(mktemp -d -t intake-e2e-XXXXXX)
PIDS=()
STARTED_DB=0

cleanup() {
  status=$?
  # each service is its own process group, so the node/python children go with their wrapper
  for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill -- "-$pid" 2>/dev/null || true; done
  if [ "$STARTED_DB" = 1 ]; then docker compose -p intake-e2e down -v >/dev/null 2>&1 || true; fi
  if [ "$status" != 0 ]; then echo "e2e failed; server logs are in $WORK" >&2; else rm -rf "$WORK"; fi
  exit "$status"
}
trap cleanup EXIT

for port in "$API_PORT" "$WEB_PORT"; do
  if (echo >"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    echo "port $port is already in use; set E2E_API_PORT / E2E_WEB_PORT" >&2; exit 1
  fi
done

if [ -z "${E2E_DATABASE_URL:-}" ]; then
  if (echo >"/dev/tcp/127.0.0.1/$DB_PORT") 2>/dev/null; then
    echo "port $DB_PORT is already in use; set E2E_DB_PORT (a database that is not ours must never be reset)" >&2; exit 1
  fi
  DB_PORT=$DB_PORT docker compose -p intake-e2e up -d db
  STARTED_DB=1
  until docker compose -p intake-e2e exec -T db pg_isready -U intake -d intake >/dev/null 2>&1; do sleep 1; done
  E2E_DATABASE_URL=postgresql+psycopg://intake:intake@127.0.0.1:$DB_PORT/intake
fi

mkdir -p "$WORK/storage" "$WORK/recorded"
export DATABASE_URL=$E2E_DATABASE_URL STORAGE_DIR=$WORK/storage
export BANK_ENCRYPTION_KEY
BANK_ENCRYPTION_KEY=$(cd apps/api && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export APP_ENV=test VALIDATION_TODAY=2026-09-25
export LLM_PROVIDER=recorded RECORDED_DIR=$WORK/recorded EXTRACTION_MODEL=claude-sonnet-5 ANTHROPIC_API_KEY=
export API_TOKEN=e2e-api-token-e2e-api-token REVIEWER_USERNAME=reviewer REVIEWER_PASSWORD=e2e-reviewer-pass
export SESSION_SECRET=e2e-session-secret-e2e-session-secret
export WORKER_POLL_INTERVAL_S=1

echo "== migrate and load the seed invoices (recorded answers) =="
(cd apps/api && uv run alembic upgrade head >/dev/null)
FIXTURES_DIR=$RECORDED_DIR HOLD_OUT=$UPLOAD_FILE uv run --project apps/api python scripts/load-demo-pipeline.py

start() { # LOG DIR COMMAND...: run in the background as its own process group
  local log=$1 dir=$2; shift 2
  setsid bash -c 'cd "$1" && shift && exec "$@"' _ "$dir" "$@" >"$WORK/$log" 2>&1 &
  PIDS+=($!)
}

echo "== start the API, the worker and the web app =="
start api.log apps/api uv run uvicorn intake.main:app --port "$API_PORT"
start worker.log apps/api uv run python -m intake.worker
if [ "${E2E_SKIP_BUILD:-}" != 1 ]; then (cd apps/web && API_URL=http://127.0.0.1:$API_PORT pnpm build >"$WORK/build.log" 2>&1); fi
API_URL=http://127.0.0.1:$API_PORT COOKIE_INSECURE=1 start web.log apps/web pnpm start -p "$WEB_PORT"

for url in "http://127.0.0.1:$API_PORT/health" "http://127.0.0.1:$WEB_PORT/login"; do
  for _ in $(seq 1 60); do curl -fs -o /dev/null "$url" && break; sleep 1; done
  curl -fs -o /dev/null "$url" || { echo "$url did not come up" >&2; exit 1; }
done

echo "== run the browser tests =="
cd apps/web
E2E_BASE_URL=http://127.0.0.1:$WEB_PORT E2E_UPLOAD_FILE=$ROOT/data/seed/invoices/$UPLOAD_FILE \
  E2E_REVIEWER_PASSWORD=$REVIEWER_PASSWORD pnpm e2e "$@"
