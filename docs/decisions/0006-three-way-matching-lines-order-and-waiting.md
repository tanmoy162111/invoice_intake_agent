# 0006 — 3-way matching: line pairing, quantity rule, cumulative billing and waiting
Date: 2026-09-25 · Status: accepted

## Context
Playbook 6.6 says to find the PO, match invoice lines to PO lines (SKU, then description, then amount),
compare price and quantity against tolerances, compare billed with received quantity, and track the
amount billed against each PO across invoices. It leaves open how to treat a line that fits two PO lines,
whether billing *less* than ordered is a variance, what "earlier" means for running totals, and what to do
when an earlier invoice cannot be read.

## Decision
1. **PO lookup.** By number (`A-Z0-9` only, case and punctuation ignored). A number that is missing from
   the system, belongs to another supplier, or names a PO that is not open is `PO_NOT_FOUND`. If the invoice's
   supplier is unknown the result is `skipped` (`SUPPLIER_UNKNOWN`); if two of the supplier's POs share the
   normalized number it is `skipped` (`AMBIGUOUS_PO`). With no number, only the supplier's *open* POs in the
   invoice's currency whose total is within `match_po_total_tolerance_bp` (2%) of the subtotal are
   candidates: one is used for the other checks but the `NO_PO` result is `skipped` (`PO_INFERRED`), none is
   `NO_PO`, several is `skipped` (`AMBIGUOUS_PO`). The subtotal used to infer a PO is the printed one, never a sum of lines.
2. **Line pairing.** SKU (a SKU on several PO lines pairs nothing), then description similarity (token-sorted, at least 80), then an identical
   amount. A PO line is used at most once. A line that could be either of two PO lines (a description
   score within 5 points, or several PO lines with the same amount) is **not guessed**; it stays unpaired
   and is reported as `LINE_NOT_ON_PO` under `QTY_VARIANCE`. A line whose SKU differs from a PO line's SKU
   is never paired with it by description or amount (a substitution, not a rewording).
3. **Quantity.** Billed quantity plus what earlier invoices billed on that PO line may not exceed the PO
   quantity (tolerance `match_qty_tolerance_bp`, default 0). Billing less is a partial invoice and passes.
   Price may differ from the PO price by `match_price_tolerance_bp` (200) in either direction.
4. **Receipts.** No receipt for the PO is `RECEIPT_MISSING` (and `QTY_NOT_RECEIVED` is `skipped`, reason
   `NO_RECEIPT`, so one fact is not reported twice). Otherwise billed plus earlier billing may not exceed the
   quantity received across all receipts.
5. **Cumulative billing.** PO total is net of tax, so it is compared with invoice *subtotals* (falling back
   to the sum of line amounts only for this check). Earlier subtotals and per-line quantities come from
   stored results (`PO_OVERBILLED.details.billed_minor`, `invoice_lines.matched_po_line_id`), so no earlier
   invoice is re-matched. Invoices in `failed` or `rejected` status are not counted.
6. **Order and waiting.** As in ADR 0005: "earlier" is received order (creation time, then id). The stage
   waits (a deferral) while any earlier invoice is unread or, in `checking`, not yet matched. After
   `MATCH_MAX_WAIT_S` (300 s) a check that depends on earlier billing and would have passed is `skipped`
   (`EARLIER_INVOICES_STILL_PENDING`). If an earlier invoice matched to the *same PO* has an unreadable
   quantity or amount, the same checks are `skipped` (`EARLIER_BILLING_UNKNOWN`), counted per PO.
7. **Credit notes.** A negative quantity, amount or subtotal is `skipped` (`CREDIT_NOTE`) for every check
   after the PO lookup, is not paired to PO lines, and is not counted in later invoices' billing (its billed
   amount is unknown), so it can never make room for an over-billing.
   **Currency.** Amounts are never compared across currencies: price and over-billing checks are `skipped`.
   `CURRENCY_MISMATCH` stays a validation check (M4).
8. **Every code always writes a result**, so the audit trail shows what was compared; a check that cannot be
   done is `skipped` with a reason, never `pass`.
9. Four seed invoices (`inv-004`, `-064`, `-075`, `-077`) fail `PO_OVERBILLED` as a knock-on of a planted
   line-amount typo that inflates the printed subtotal above the PO total. The flag is correct by the
   printed numbers. The fixed golden set is not edited; the acceptance test carries one explicit allowance.

## Why
Comparing the wrong two lines, or a total that leaves out earlier invoices, is how a bad invoice would
clear. Refusing to guess costs a person a glance; a wrong guess costs money.

## Consequences
- Invoices with unusual wording and no SKU may stay unpaired and go to a person (safe, slightly noisy).
- One invoice with an unreadable amount makes later invoices on the same PO "could not check" for the
  cumulative checks until it is corrected (M8) and matching is re-run.
- Routing (M7) must treat `fail` and `skipped` on money-related match checks as needs-review.
