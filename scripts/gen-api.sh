#!/usr/bin/env bash
# Regenerate the web app's API types from the running API's OpenAPI schema (the schema needs the token).
# Usage: API_OPENAPI_URL=http://localhost:8001/openapi.json make gen-api   (reads API_TOKEN from .env)
set -euo pipefail
cd "$(dirname "$0")/.."
url="${API_OPENAPI_URL:-http://localhost:8000/openapi.json}"
token="${API_TOKEN:-$(grep -E '^API_TOKEN=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)}"
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
curl -sf -H "Authorization: Bearer ${token}" "$url" -o "$tmp" || { echo "could not fetch $url (is the API running? is API_TOKEN set?)" >&2; exit 1; }
(cd apps/web && pnpm exec openapi-typescript "$tmp" -o src/lib/api/schema.d.ts)
