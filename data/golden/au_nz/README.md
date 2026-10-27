# Australian and New Zealand golden supplement

Eight synthetic invoices (4 AUD, 4 NZD) that exercise the currencies the main golden set cannot:
bare `$`, `A$` / `NZ$` and code (`AUD 1,234.50`) amounts, GST at 10% and 15%, clean, scanned and
photographed. They are separate from the 60-invoice golden set in `../` on purpose, so that set,
its tests and published numbers do not change.

- `invoices/` and `truth/`: the documents and their answer keys (same format as `../truth`).
- `master.json`: the suppliers, purchase orders and receipts they refer to, plus AUD and NZD
  approval limits. Load it **after** `data/seed/master.json`; without it the suppliers are unknown.
- Everything is synthetic. Tax numbers pass the real check-digit rules but are test data.
- Regenerate with `make generate-au-nz`. Existing files are never overwritten.
- `make eval` does not read this folder yet. When the evaluation harness (M10) is merged it has to be
  pointed at `au_nz/truth` and `au_nz/master.json`; until then these files are inert.
