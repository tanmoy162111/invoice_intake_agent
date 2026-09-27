# 0011 — Evaluation harness: reuse the real pipeline, reconstruct facts from the database
Date: 2026-09-28 · Status: accepted

## Context
Playbook §8/M10 asks for honest, published accuracy numbers on the 60-document golden set, split by
field and by document quality, with the false clear rate as the headline number (must be 0%), plus a
free CI subset and a regression gate. The harness must never guess a value it can't verify, must
never call a model in CI, and must not become a second, divergent implementation of the pipeline.

## Decision
1. **`eval/run_eval.py` drives the real pipeline**, not a shortcut: it ingests each golden document
   through `UploadIngestor` and drains the real job queue with the project's actual `HANDLERS`
   (`make_extract_handler()`'s default client factory is already `build_client`, so the configured
   `LLM_PROVIDER`/`EXTRACTION_MODEL` is used automatically - no eval-specific model-selection code).
2. **Facts for comparison are read back from the database** after the pipeline settles (`Invoice`,
   `InvoiceLine`, `FieldExtraction`, `InvoiceException`, `LlmCall` rows), reassembled into a real
   `core.extraction.Interpreted` object, and handed to the same `core/eval_metrics.compare_invoice`
   the unit tests exercise. This is deliberate: the eval harness measures what actually landed in the
   database, the same place a reviewer or an export would read from, not an in-memory value that
   never got that far.
3. **The bank account is decrypted for comparison only.** `field_extractions.normalized_value` for
   `supplier_bank_account` is a keyed hash by design (never the account, even here); the eval script
   decrypts the sealed raw value with the configured `BankVault` and normalizes it the same way the
   pipeline does, so bank-account accuracy is still measured, without weakening what's actually
   stored.
4. **The job drain waits out a transient failure's backoff** instead of stopping the moment nothing
   is immediately claimable (`run_once` alone does that; a live model call can transiently fail and
   retry on its own schedule).
5. **The CI subset (`make eval-ci`) replays real, previously-captured answers** committed under
   `data/golden/recorded/`, keyed by request hash exactly like `RecordedClient` already works
   elsewhere - not the synthetic "perfect reader" fixtures other integration tests use. It costs
   nothing and never touches the network, but it is real model output, so it catches a real
   regression in parsing or comparison logic, not just a synthetic round-trip.
6. **A non-Anthropic run is labelled in the report itself** (`⚠ Non-reference build`), not just in a
   doc somewhere: the model and provider are in the headline, so a report from `make eval` run with
   `LLM_PROVIDER=openrouter` (or `ollama`) can never be mistaken for the Claude-based reference build
   the playbook's accuracy claims are about (ADR 0010).
7. **Pipeline tables are reset before each run** (same as `scripts/load-demo-pipeline.py`): `make
   eval` and `make eval-ci` are meant for a database you don't mind resetting, documented in the
   script's own docstring and the manual, not a new, different convention.

## Why
Reusing the real ingestion path and the real job handlers means an eval run genuinely exercises the
same code a real invoice goes through - a bug that only shows up in the real worker path, or in real
row-locking, would still surface here, unlike a hand-rolled shortcut that calls `interpret()` directly
and skips storage. Reading facts back from the database, rather than capturing the pipeline's
in-memory output as it runs, keeps the harness decoupled from the pipeline's internals: it can be
re-run against however invoices ended up in the database, including by a future milestone that adds
another way invoices get there.

## Consequences
- The harness is slower than a hand-rolled shortcut would be (real job processing, one invoice at a
  time, no worker concurrency in the script itself): about 40s/invoice observed with a 72B vision
  model over OpenRouter, so a full 60-document run takes on the order of 30-40 minutes wall clock.
- `data/golden/recorded/` fixtures are tied to the model and prompt version they were captured with
  (the request hash includes both); changing either means recapturing them, the same way any other
  `RecordedClient` fixture works.
- `eval_runs` rows land in whatever `DATABASE_URL` the run used; since that is typically a database
  meant to be reset, historical `eval_runs` rows are not guaranteed to survive across runs. The
  durable artifact is the committed markdown report in `eval/reports/`.
