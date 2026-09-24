# CLAUDE.md — rules for working in this repo

Invoice Intake Agent (BeyondAI Demo A). The full spec is in `docs/playbook.md`.

## Always
- Read the playbook and the current GitHub issue before changing code.
- Work only on the current issue. If you find other problems, list them in the PR; don't fix them.
- Propose a plan and wait for approval before large changes.
- Keep all decision logic (validation, dedupe, matching, routing) in `apps/api/src/intake/core/` as pure functions with unit tests. No DB, network, or file I/O there.
- Money is integer minor units + ISO currency. Never floats.
- Every state change writes an audit event.
- Update docs in the same PR as code. Every milestone PR also updates `docs/report.md` (a chapter with all three layers: plain words, how it works, under the hood) and `docs/manual.md`; `test_docs.py` guards them.
- Run `make check` before saying a task is done.

## Never
- Never add code that pays, transfers, or moves money.
- Never update or delete `audit_events`.
- Never commit real client data, secrets, or `.env` files.
- Never call the model API in unit or CI tests; use recorded fixtures.
- Never change a prompt, model, threshold, or rule without running `make eval` and reporting the result.
- Never guess an API detail for the Anthropic SDK; check https://docs.claude.com.

## Conventions
- **Python:** ruff (lint + format), type hints everywhere, mypy strict on `core/`, Pydantic models at every boundary.
- **TypeScript:** strict mode, no `any`, API types generated only from OpenAPI (`pnpm gen:api`).
- **Naming:** exception codes `UPPER_SNAKE_CASE`; statuses `lower_snake_case`; DB tables plural.
- **Tests:** `core/` coverage ≥ 90%; every bug fix starts with a failing test; write `core/` tests first.
- **Exceptions:** every code needs an explanation template, a suggested fix, and a test (see playbook §7).
- **Branches:** one per task, e.g. `feat/m3-extraction`. **Commits:** conventional (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Uncertain means human: if a check fails or confidence is low, route to review. False clear rate must stay 0%.

## Commands
- `make dev`         # start everything
- `make seed`        # load demo data
- `make check`       # lint + types + tests
- `make eval`        # full evaluation (costs money; ask first)
- `make demo-reset`  # reset to the clean demo state
