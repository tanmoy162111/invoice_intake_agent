#!/usr/bin/env bash
# PostToolUse (Write|Edit): format the edited file with the project's own tools.
# Silent no-op until the toolchain exists (apps/api with ruff, apps/web with prettier).
f=$(jq -r '.tool_response.filePath // .tool_input.file_path // empty')
[ -z "$f" ] || [ ! -f "$f" ] && exit 0

root="${CLAUDE_PROJECT_DIR:-$(pwd)}"

case "$f" in
  "$root"/apps/api/*.py)
    cd "$root/apps/api" || exit 0
    [ -f pyproject.toml ] || exit 0
    uv run --quiet ruff check --fix --quiet "$f" >/dev/null 2>&1
    uv run --quiet ruff format --quiet "$f" >/dev/null 2>&1
    ;;
  "$root"/apps/web/*.ts|"$root"/apps/web/*.tsx|"$root"/apps/web/*.js|"$root"/apps/web/*.jsx|"$root"/apps/web/*.css|"$root"/apps/web/*.json)
    case "$f" in */node_modules/*|*/.next/*|*/lib/api/schema.d.ts) exit 0 ;; esac
    cd "$root/apps/web" || exit 0
    [ -d node_modules/.bin ] && [ -x node_modules/.bin/prettier ] || exit 0
    pnpm exec prettier --write --log-level silent "$f" >/dev/null 2>&1
    ;;
esac

exit 0
