# 0005 — Duplicates: only the later invoice is flagged, and it waits for earlier ones to be read
Date: 2026-09-25 · Status: accepted

## Context
Playbook 6.5 defines hard duplicates (same supplier, same normalized number) and soft duplicates (same
supplier, total, date within 7 days, number similarity >= 85). Two practical questions are open: which of
two matching invoices is "the duplicate", and what happens when the earlier invoice has not been read yet.
The seed also has a pair (`inv-095` and `inv-006`) that matches the soft rule exactly but is labelled as
a PO over-billing case, not a duplicate.

## Decision
1. Only the **later-received** invoice is compared, against earlier ones; the original is never flagged.
   The result names the earlier invoice (id, number, date, similarity, days apart) for a side-by-side view.
2. Before comparing, the stage **waits** (a deferral: no attempt is used, the job shows as paused) while
   any earlier invoice of the tenant is still `received`, `extracting` or `extracted`, because its
   fields and supplier link are not final. After `DEDUPE_MAX_WAIT_S` (300 s) it proceeds; a clean
   comparison is then reported as `skipped` (`EARLIER_INVOICES_STILL_PENDING`), never as a pass.
3. Matching is done on `A-Z0-9` only (case, spaces and punctuation ignored), with leading zeros removed
   from digit runs (`INV-0043` equals `INV-43`). A soft match compares the
   whole normalized numbers, or their digits when both have at least four, so `1043`, `INV1043` and
   `INV-1043` still match. Totals must be equal *with sign*, so a credit note is not a duplicate of its
   invoice.
4. **Same supplier** means the same supplier record, or, where a record is missing on either side, the same
   printed name. A linked invoice and an unlinked one with different names cannot be reconciled: if their
   numbers are also close the result is `skipped` (`SUPPLIER_UNCERTAIN`), never a pass.
5. If the comparison cannot be done (no supplier, no number, or a similar invoice is missing its total, date
   or currency; a zero total is not evidence) the result is `skipped`.
6. The `inv-095` pair is flagged. It repeats `inv-006` in every field but one digit of the number, so
   it is a real soft duplicate. The label in the fixed golden set is too narrow; the golden set is not
   edited here, and the acceptance test carries one explicit, documented allowance.

## Why
A real duplicate must never pass by accident, and the original must not be blamed for its copy. Waiting
is cheaper and safer than a race in which the copy is checked before the original is readable.

## Consequences
- Sequential numbers from one supplier (0635, 0636) are at least 85% similar, so an invoice with an
  unread total or currency beside a similar-numbered earlier invoice is `skipped` (needs a person).
- One wait-limit expiry can hide a duplicate behind a pending invoice; the result says so.
- Routing (M7) must treat `fail` and `skipped` `POSSIBLE_DUPLICATE` as needs-review.
