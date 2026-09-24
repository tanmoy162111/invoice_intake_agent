#!/usr/bin/env bash
# Create .env from .env.example if missing, and fill generated secrets that are still empty.
# Generated values are for local/demo use only. Sourced or run by the other scripts.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { cp .env.example .env; echo "created .env from .env.example"; }
chmod 600 .env

set_if_empty() { # KEY GENERATOR-COMMAND
  local current; current=$(grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true)
  [ -n "$current" ] && return 0
  local value; value=$(eval "$2")
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$value|" .env; else printf '%s=%s\n' "$1" "$value" >> .env; fi
  echo "generated $1 in .env"
}

set_if_empty BANK_ENCRYPTION_KEY \
  '(cd apps/api && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")'
set_if_empty API_TOKEN 'python3 -c "import secrets; print(secrets.token_urlsafe(32))"'
