# 0013: A printed dollar sign means the supplier's own dollar, and A$ / NZ$ name theirs

Status: accepted (2026-10-27). Follows ADR 0012, which added AUD and NZD as printed codes only.

## Context

Most Australian and New Zealand invoices print amounts with a bare `$`. The system read a bare `$` only as USD:
`$1,234.50` failed to parse as AUD, and `normalize_currency("$", hint="AUD")` was "ambiguous" even for a supplier
whose usual currency is AUD. Those invoices went to a person ("uncertain means human"), which is safe but made
touchless Australian and New Zealand processing impossible. The AUD/NZD golden supplement showed it: six of eight
invoices failed on exactly this.

## Decision

- `A$`, `AU$` and `NZ$` are unambiguous and name AUD and NZD. (Not `CA$`, `US$`, `S$`: unsupported, as before.)
- A bare `$` is read as the supplier's own currency **only when that is USD, AUD or NZD** (the existing hint rule,
  widened from USD alone). With no supplier, an unknown supplier, a supplier with several currencies, or a non-dollar
  supplier it is still ambiguous and goes to a person. `¥` is unchanged.
- A symbol on an amount must be one the invoice's currency can be written with: `$` for USD/AUD/NZD, `A$`/`AU$` for
  AUD, `NZ$` for NZD. Any other combination (`A$` on an NZD invoice, `$` on a EUR invoice) is refused, never converted.
- Confidence: `A$`/`NZ$` corroborate AUD/NZD; a bare `$` is "not evidence either way" (as for USD); another dollar
  contradicts. The currency check (`CURRENCY_MISMATCH`) treats a bare `$` as consistent with any dollar supplier.

## Not changed

No prompt, model, threshold or other rule. No exchange rates: a dollar total is only ever compared with its own
currency's limit.

## Evidence and limits

- Before: of the eight golden AUD/NZD invoices, the six printing `$`, `A$` or `NZ$` were misread (currency
  ambiguous, amounts unread, held for review with false exceptions); the two printing a code were read exactly.
- After: all eight are read exactly and the six clean ones clear straight through (`tests/integration/test_au_nz_pipeline.py`,
  real pipeline, a perfect-reader answer, no model). All 1127 + 218 earlier tests still pass.
- **This does not measure a real model.** The perfect-reader answer assumes the model reports the printed currency
  text. How a real model reads these documents needs the paid `make eval` with the harness pointed at
  `data/golden/au_nz`; that has not been run.
