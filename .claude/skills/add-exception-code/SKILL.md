---
name: add-exception-code
description: Add a new exception code end to end - enum, severity, explanation template, suggested fix, unit test, and taxonomy doc row. Use when adding or changing an entry in the exception taxonomy.
disable-model-invocation: true
argument-hint: <CODE_NAME> [severity: info|review|block]
---

# Add an exception code

Add exception code `$ARGUMENTS` following `docs/playbook.md` §6.7, §7 and M7. Every code needs all five artifacts below; do not finish with any missing.

## Before writing code
1. Read playbook §7 (taxonomy and severity meanings) and the existing `apps/api/src/intake/core/exceptions.py`, `docs/exception-taxonomy.md`, and the existing tests for a similar code. Match their style.
2. If the code name or severity is not given, or it overlaps an existing code, ask before continuing.
3. Confirm which check produces it (the rule in `core/validate.py`, `dedupe.py`, or `match.py`) and what numbers that check exposes in its `details`. The explanation can only use facts the check actually records.
4. If this changes a rule or routing outcome, remind the user that `make eval` and a report are required before merge (CLAUDE.md). Do not run it without asking; it costs money.

## Artifacts (all required)
1. **Enum entry** in `core/exceptions.py`: `UPPER_SNAKE_CASE`, single enum, with its severity (`info` | `review` | `block`).
2. **Explanation template**: built deterministically from the check `details`. It must contain the real numbers (amounts, quantities, percentages, dates, identifiers), never placeholders or free-form model text. Format money from integer minor units plus currency.
3. **Suggested fix**: one actionable sentence. For `block` codes state that a resolution note is required. Fraud-related codes (like bank details) must say to verify by phone with a known contact.
4. **Unit tests** in `tests/unit/` (no DB, no network): the exception is raised on failure, not raised on pass, the explanation contains the exact expected figures, and the severity and suggested fix are correct. Write these first and watch them fail.
5. **Docs**: add the row (code, severity, example explanation, suggested fix) to `docs/exception-taxonomy.md` in the same change.

## Routing check
Confirm `core/routing.py` handles the new severity correctly: `review` and `block` open exceptions must prevent `cleared`; `block` must also require a resolution note before approval. Add or update a routing test if the code changes any of that.

## Finish
- Keep pure logic in `core/` (no I/O, no clock, no floats for money).
- Run `make check` (lint, types, tests) and report the actual output.
- Summarize the five artifacts with file paths.
