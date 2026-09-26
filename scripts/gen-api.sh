#!/usr/bin/env bash
# Regenerate the web app's API types from the API's OpenAPI schema.
# Default: build the schema straight from the code (no server, no token needed), write the snapshot
# apps/web/src/lib/api/openapi.json and generate schema.d.ts from it. A test fails if the snapshot is
# out of date. To generate from a running API instead: API_OPENAPI_URL=http://localhost:8000/openapi.json
# (reads API_TOKEN from .env).
set -euo pipefail
cd "$(dirname "$0")/.."
snapshot=apps/web/src/lib/api/openapi.json
if [ -n "${API_OPENAPI_URL:-}" ]; then
  token="${API_TOKEN:-$(grep -E '^API_TOKEN=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)}"
  curl -sf -H "Authorization: Bearer ${token}" "$API_OPENAPI_URL" -o "$snapshot" || { echo "could not fetch $API_OPENAPI_URL (is the API running? is API_TOKEN set?)" >&2; exit 1; }
else
  (cd apps/api && uv run python -c "
import json
from intake.config import Settings
from intake.main import create_app
app = create_app(Settings(api_token='x', session_secret='', reviewer_password=''))
print(json.dumps(app.openapi(), indent=2, sort_keys=True))
") > "$snapshot"
fi
(cd apps/web && pnpm exec openapi-typescript src/lib/api/openapi.json -o src/lib/api/schema.d.ts)
