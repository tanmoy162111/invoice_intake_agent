# Architecture notes

The overall design is in [`playbook.md`](playbook.md) §3. This page records what is built.

## Data model (M1)

All 17 tables from playbook §5.1 exist (migrations `0002` and `0003`).

- **Tenancy:** every business table has a non-null `tenant_id`. `eval_runs` is global. A test
  fails if a new table is added without one.
- **Money:** integer minor units plus an ISO currency code (`*_minor` columns, `char(3)` currency).
  Parsing and arithmetic helpers are in `core/money.py`. No floats anywhere.
- **Closed vocabularies** (statuses, routes, actor types, exception codes, ...) are enums in
  `core/` and are enforced in Postgres with `CHECK` constraints.
- **Audit log:** `audit_events` is append-only. A trigger rejects `UPDATE`, `DELETE` and
  `TRUNCATE` (migration `0003`), and the only writer is `audit/writer.py::record_event`.
- **Bank details:** stored encrypted (Fernet, key `BANK_ENCRYPTION_KEY`) in
  `suppliers.bank_account_encrypted`; comparisons use `bank_account_hash`, a keyed HMAC of the
  normalised account number, so plaintext is never needed to compare. `security.BankVault`.
- **Migrations are immutable** once committed: add a new revision instead of editing one.

## Synthetic data (M1)

`data/generator/` builds a deterministic demo world (fixed seed): 15 suppliers, 104 POs with
receipts, and 120 invoices rendered in 5 layouts and 3 qualities (clean PDF, scanned, photo),
with planted problems covering every exception code. See `data/seed/MANIFEST.md`.

- `make generate` rebuilds `data/seed/` and `data/golden/`. Output is deterministic.
- `data/seed/truth/*.json` is the ground truth for each document. `expected.must_raise` are the
  planted problems; `may_raise` are acceptable knock-on codes (`*` = anything, unreadable file).
- The golden set (60 invoices, chosen by a fixed rule) is copied to `data/golden/` once and never
  overwritten. Do not use it while writing prompts.
- Conventions the truth follows, which validation and matching must implement the same way:
  PO totals are net of tax and compared with the invoice subtotal; the approval limit applies to
  the invoice total including tax; a document only carries what is printed on it (a field a layout
  does not print is `null` in the truth).

`make seed` loads suppliers, POs and receipts into Postgres (idempotent, audit-logged) and copies
the invoice files (not the truth) to `data/inbox/`, ready to upload once ingestion exists (M2).
