#!/usr/bin/env bash
# make ingest-inbox: feed every file in data/inbox through ingestion (same path as an upload).
# Runs inside the api container so files land in the storage volume the worker reads from.
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/ensure-env.sh
[ -d data/inbox ] || { echo "data/inbox is missing: run 'make seed' first" >&2; exit 1; }
docker compose run --rm --build -v "$PWD/data/inbox:/inbox:ro" api \
  sh -c "alembic upgrade head && python -m intake.ingest.folder --inbox /inbox"
