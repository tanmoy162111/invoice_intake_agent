# 0003 — Extraction: cached answers, a daily spend cap, and untrusted model output
Date: 2026-09-25 · Status: accepted

## Context
Reading an invoice with a model costs money and the answer is not always in the shape we asked for.
Documents can contain text written to steer the model. Re-running a stage (retries, crashes) must not
pay twice, and a bug or a large batch must not run up an unbounded bill.

## Decision
1. Save every validated answer in `llm_calls.response`, keyed by (file hash, model, prompt version, a
   fingerprint of the prompt text and answer schema), scoped to the tenant. A hit makes no call and no row.
2. Write each `llm_calls` row in its own transaction, before the invoice is updated.
3. Cap model spend per UTC day (`DAILY_SPEND_CAP_USD`, default 5). Check before every call. At the cap,
   extraction jobs are deferred to 00:00 UTC (not failed) and the API says so.
4. Treat the model's answer as untrusted: a strict tool schema, then Python validation (no NUL, bounded
   length, page numbers inside the document), then normalization. Ambiguity becomes null with zero
   confidence. The bank account is sealed inside the cached answer.
5. Ask again at most once when the answer does not fit, then fail the invoice with a reason.

## Why
A paid answer must never be lost or bought twice. A deferral loses nothing, while a failure would need
manual re-queueing. Model output can be wrong, truncated or hostile, and only code we wrote decides what
reaches the database. A guess that looks right is worse than a blank a person fills in.

## Consequences
- Concurrent workers can overshoot the cap by one call each (the check is not a reservation).
- `llm_calls.response` holds supplier data in plain text: see `data-handling.md`.
- Changing the prompt or the schema changes the fingerprint, so old answers are simply not reused.
- A rotated `BANK_ENCRYPTION_KEY` makes old cached answers unreadable; they are ignored and re-asked.
