#!/usr/bin/env bash
# make seed: start the db, migrate, load master data, stage invoice files in data/inbox.
# Safe to re-run. Values come from the environment, then .env, then defaults, so
# `DB_PORT=5434 make seed` works without editing .env.
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/ensure-env.sh
source scripts/_env.sh

export DB_PORT; DB_PORT=$(envval DB_PORT 5432)
export POSTGRES_PASSWORD; POSTGRES_PASSWORD=$(envval POSTGRES_PASSWORD intake)
docker compose up -d --wait db

export DATABASE_URL="postgresql+psycopg://intake:${POSTGRES_PASSWORD}@localhost:${DB_PORT}/intake"
export BANK_ENCRYPTION_KEY; BANK_ENCRYPTION_KEY=$(envval BANK_ENCRYPTION_KEY "")
cd apps/api
uv run alembic upgrade head
uv run python -m intake.seed "$@"
