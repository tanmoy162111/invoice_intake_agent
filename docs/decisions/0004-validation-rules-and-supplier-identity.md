# 0004 — Validation: a supplier is known only by exact identifiers, and "cannot check" is never a pass
Date: 2026-09-25 · Status: accepted

## Context
Playbook 6.4 says a supplier is known by an exact tax id, "else a fuzzy name/alias match >= 90".
The seed data plants look-alike suppliers (for example "Coastal Packing Ltd." against the real
"Coastal Packaging Ltd.", 95% similar, printing no tax id) that must raise `UNKNOWN_SUPPLIER`. A
near-identical name from an unverified sender is also a classic impersonation pattern.

## Decision
1. A supplier is **known** only by an exact tax id, or an exact name or alias (ignoring case and
   punctuation), and every identifier that is printed must agree. A tax id that belongs to another
   supplier, or a known name beside a different tax id, is a **conflict**, not a match.
2. A fuzzy score (>= `supplier_fuzzy_min`, default 90) only **suggests** the closest supplier.
3. Every check yields `pass`, `fail` or `skipped`. `skipped` (a value was missing or unreadable) is
   never a pass. The total check passes only when subtotal + tax was compared with the total.
4. "No bank account printed" passes only when the model was sure; an unread account is `skipped`.
5. The bank account is compared as a keyed hash; hashes and account numbers never appear in results.
6. The supplier's tax rate lives in master data (`suppliers.tax_rate_bp`) and is the expected rate
   for the tax check; line rates are the fallback.

## Why
False clears must stay at 0%. On the seed, exact name or alias resolves all 115 on-file invoices and
none of the decoys. OCR errors in a real supplier name will go to a person, which is the safe side.

## Consequences
- Playbook 6.4 wording differs (fuzzy >= 90 does not make a supplier known).
- Routing (M7) must treat any `skipped` check on money or identity (currency, bank, supplier) and any
  `fail` as needs-review.
- Tax rates come from master data; reading the printed rate from the invoice is a later option that
  needs a prompt change and an evaluation.
