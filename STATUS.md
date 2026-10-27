# STATUS

Last updated: 2026-10-27. Branch `feat/b1-xero-handoff` (from `main`).

## Project B: accounting handoff

**Implementation status: partially complete.** Built and tested against a mock provider and against
recorded, Xero-shaped responses. **Never run against Xero or the Xero Demo Company.** Not production-ready.

> Creates draft bills only. A human must review and approve bills in the accounting platform.

### Works (tested)
- An approved invoice can be sent as a draft bill through the API and the invoice page (offline mock provider).
- Non-approved invoices never reach the provider; approval is re-checked right before the write.
- Repeated, simultaneous and crashed requests create at most one bill; an unclear outcome becomes `unknown` and
  is settled by a safe lookup or a person, never by a blind retry.
- Unsafe invoices are stopped before any write: missing fields, totals or tax that do not add up, unmapped tax
  rates, ambiguous or conflicting suppliers, an existing bill with the same number.
- Xero adapter: `PUT /Invoices` with `Status: DRAFT` only; failures classified; tokens encrypted and refreshed
  under a lock; OAuth state and PKCE checked.
- Every step is audited (codes and ids only). No approve, pay or void method exists anywhere.

### Not done / not proven
- No real Xero contact. Unverified API details are listed in `docs/connectors.md`.
- No credentials or Xero developer account exist for this project; creating one and accepting Xero's terms is a
  decision for the owner.
- No web screen for connecting Xero (the API routes exist). The web panel has not been exercised in a browser.
- MYOB not built (extension point documented). Contacts are never created; expense account left blank.
- Credit notes and zero-value invoices are not sent. Any signed-in reviewer can trigger a handoff (no roles).
- AUD and NZD are accepted only as printed codes. A printed `$`, `A$` or `NZ$` is **not** read as AUD or NZD
  (a bare `$` means USD only), so those invoices go to a person. Fixing it changes a shared rule and needs an
  evaluation. Two strict `xfail` tests in `tests/unit/test_au_nz_golden.py` pin the desired behaviour.
- `make eval` (paid) not run. The free recorded subset (3 of 60) gave identical accuracy with and without AUD/NZD.
- `data/golden/au_nz/` holds 8 synthetic AUD/NZD golden invoices; the evaluation harness (M10, unmerged) does not
  read them yet.

### Evidence
See "Test report" below for commands and results.

### Human action needed
1. Decide whether to create a Xero developer app (free Starter tier per search results; verify) and use the Demo Company.
2. Provide `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET`, `XERO_REDIRECT_URI`, `ACCOUNTING_ENCRYPTION_KEY`, `ACCOUNTING_TAX_TYPES` in `.env`.
3. Run the sandbox check and verify the open points in `docs/connectors.md`.
4. Decide whether to run `make eval` after the currency change.

## Test report
Run on 2026-10-27 against a throwaway local Postgres 16 container (port 5434); `LLM_PROVIDER=anthropic` was
set on the command line because the developer `.env` names a provider that only exists on the M10 branch.

| Command | Result |
|---|---|
| `cd apps/api && uv run ruff check . ../../data/generator && uv run ruff format --check . ../../data/generator && uv run mypy` | passed (80 source files) |
| `cd apps/api && uv run pytest -q --cov=intake.core --cov-fail-under=90` | **1591 passed**, 0 failed, 2 warnings; `core/` coverage 99% (`core/accounting.py` 100%) |
| `cd apps/web && pnpm lint && pnpm typecheck && pnpm test && pnpm build` | passed (51 web unit tests) |
| `bash scripts/gen-api.sh` | API snapshot and types regenerated; the snapshot test passes |

After the AUD/NZD golden supplement (branch `feat/au-nz-golden`): **1626 passed, 4 xfailed** (the 4 are the
strict `$` / `A$` / `NZ$` tests that fail on purpose today), lint, format and mypy clean, `core/` coverage 99%.

Baseline before Project B on `main`: 1127 unit + 218 integration tests, all passing. Project A and C
regression suites are part of the 1591 and pass. The M10 evaluation harness lives on another branch and was
not touched or run here.

**Not run:** `make e2e` (needs Docker and Chromium; the web panel is untested in a browser); `make eval`
(costs money, asks first); any sandbox test against Xero (no credentials). A first full run failed
because the new integration tests left rows that other suites' cleanup tripped over; fixed with an automatic
cleanup fixture and re-run clean.

**Mocked, not real:** every "created a draft bill" result above comes from the in-memory mock or recorded
Xero-shaped responses. None of it is evidence that Xero accepts these requests.
