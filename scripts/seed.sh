#!/usr/bin/env bash
# make seed: start the db, migrate, load master data, stage invoice files in data/inbox.
# Safe to re-run. Values come from the environment, then .env, then defaults, so
# `DB_PORT=5434 make seed` works without editing .env.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { cp .env.example .env; echo "created .env from .env.example"; }

envval() { # KEY DEFAULT
  if [ -n "${!1:-}" ]; then printf '%s' "${!1}"; return; fi
  local v; v=$(grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true)
  printf '%s' "${v:-$2}"
}

key=$(envval BANK_ENCRYPTION_KEY "")
if [ -z "$key" ]; then
  key=$(cd apps/api && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  if grep -q '^BANK_ENCRYPTION_KEY=' .env; then
    sed -i "s|^BANK_ENCRYPTION_KEY=.*|BANK_ENCRYPTION_KEY=$key|" .env
  else
    printf 'BANK_ENCRYPTION_KEY=%s\n' "$key" >> .env
  fi
  echo "generated BANK_ENCRYPTION_KEY in .env (demo data only)"
fi

export DB_PORT; DB_PORT=$(envval DB_PORT 5432)
export POSTGRES_PASSWORD; POSTGRES_PASSWORD=$(envval POSTGRES_PASSWORD intake)
docker compose up -d --wait db

export DATABASE_URL="postgresql+psycopg://intake:${POSTGRES_PASSWORD}@localhost:${DB_PORT}/intake"
export BANK_ENCRYPTION_KEY="$key"
cd apps/api
uv run alembic upgrade head
uv run python -m intake.seed "$@"
