# Sourced by seed.sh / ingest-inbox.sh: read a value from the environment, then .env, then a default.
envval() { # KEY DEFAULT
  if [ -n "${!1:-}" ]; then printf '%s' "${!1}"; return; fi
  local v; v=$(grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true)
  printf '%s' "${v:-$2}"
}
