# 0007 — Exceptions and routing: one per check, "could not be checked", and a strict router
Date: 2026-09-25 · Status: accepted

## Context
Playbook 6.7 turns every failed check into an `exceptions` row and routes an invoice to `cleared` only when
there is no open review or block exception, every critical field is confident and the total is within the
approval limit. M4 to M6 also produce `skipped` results ("could not check") that are never a pass, and the
taxonomy has no code for them. Several facts needed for explanations were not in the templates.

## Decision
1. **One exception per check.** A check with several findings (price on lines 2 and 4) is one exception whose
   explanation lists each line with its numbers. Explanations come only from templates in `core/exceptions.py`.
2. **A skipped check is raised under its own code**, at `review` severity (never `block`: nothing was proved),
   with the sentence "*The tax* could not be checked (*reason*), so a person needs to look at it." No new code,
   so the playbook 7 taxonomy is unchanged. A skip that is only the consequence of another raised exception
   (`UNKNOWN_SUPPLIER`, `NO_PO`, `NO_RECEIPT` as the reason) is not repeated, and a credit note is one card.
   A stored check with a code outside the taxonomy is an error, never ignored.
3. **Templates state the real facts.** Where the playbook example did not fit what the checks know, the template
   gained parameters (`TOTAL_MISMATCH` says which comparison failed; `TAX_MISMATCH` the basis;
   `POSSIBLE_DUPLICATE` the kind of match and the days between invoice *dates*, not "received N days ago";
   `CURRENCY_MISMATCH` says whether the comparison is with the supplier's usual currency or the PO;
   `PO_NOT_FOUND` says why; `QTY_VARIANCE` and `QTY_NOT_RECEIVED` count earlier billing). A second wording
   (`VARIANTS`) covers a line that is not on the PO.
4. **Routing** (`core/routing.py`) is `cleared` only if there is no open review or block exception, no doubtful
   critical field, a known non-negative total not above the limit, a configured limit, and every one of the
   fifteen expected checks has a result. A bank-account change always goes to review, whatever its severity.
   The reasons are recorded. Anything uncertain routes to review.
5. **The approval limit** is `approval_amount_limit_minor` in the tenant settings, compared with the invoice
   total in the invoice's **own** currency. There is no exchange-rate conversion; a limit that is not set
   sends every invoice to review. (Open: per-currency limits, if a client needs them.)
6. **Idempotency** is the status change: an invoice no longer in `checking` is left alone, and exceptions,
   route and status commit in one transaction. The audit log gets `exception_raised` (code, severity,
   unchecked), the `status_changed` reasons and `routing_decided`; explanations, which can name an invoice
   number, live only on the exception row.
7. **Blank documents** fail extraction and stay `failed` (playbook 5.2 allows only a retry from `failed`);
   the `UNREADABLE_DOCUMENT` block exception is raised at that moment so a reviewer sees it. The M8 queue must
   list failed invoices as well as `needs_review`.
8. `auto_approve_cleared` is not implemented: approval is a person's action (M8).

## Why
A skipped check that silently clears an invoice is the failure this system exists to prevent. Keeping the
taxonomy fixed keeps the dashboard and the review screen simple; the wording says what happened.

## Consequences
- **Touchless rate depends on confidence.** With the current confidence rules, a scanned or photographed
  document's invoice number has no text layer to confirm it and scores 75%, below the 80% minimum, so almost
  every scan and photo goes to review (24 of the 25 clearable ones in the seed set). No threshold was
  changed; measuring and tuning it needs `make eval` (M10).
- Two clearable invoices are also held because a similar earlier invoice has no readable currency, so the
  duplicate check could not be completed.
- Cross-currency limits are only as good as "one number for all currencies".
- Correcting a field (M8) must re-run the checks and the routing, and reopen exceptions that no longer apply.
