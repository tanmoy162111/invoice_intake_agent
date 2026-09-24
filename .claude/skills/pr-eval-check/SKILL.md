---
name: pr-eval-check
description: Check whether a branch or PR changes prompts, models, thresholds, or rules and, if so, whether it carries a fresh eval report that passes the regression gate (no false clears, no critical-field drop over 2 points). Use before opening or approving a PR.
disable-model-invocation: true
argument-hint: [base branch, default main] [PR number]
allowed-tools: Bash(git *), Bash(gh pr view *), Bash(gh pr diff *), Read, Grep, Glob
---

# PR eval gate check

Enforce playbook §8.3: a PR that changes a prompt, model, threshold, or rule must include a fresh eval report, and must not regress. This skill only inspects; it never runs `make eval` (real model calls cost money). Arguments: `$ARGUMENTS`.

## 1. Find what changed
- Base is the first argument, else `main`. Use `git diff --name-only <base>...HEAD`. If a PR number is given, use `gh pr diff <n> --name-only` and `gh pr view <n> --json body`.
- If this is not a git repo or the base does not exist, say so and stop.

## 2. Does it trigger the gate?
Mark the PR as **eval-required** if the diff touches any of:
- **Prompts:** `apps/api/src/intake/extract/prompts/**`
- **Model or extraction config:** `extract/llm.py`, `extract/schema.py`, `extract/confidence.py`, `EXTRACTION_MODEL` in `.env.example` or `config.py`
- **Rules:** `core/validate.py`, `core/dedupe.py`, `core/match.py`, `core/routing.py`, `core/normalize.py`, `core/exceptions.py`
- **Thresholds and tolerances:** `field_confidence_min`, `approval_amount_limit`, price/quantity tolerances, dedupe similarity limits, `max_invoice_age_days`, tenant `settings` defaults, seed settings
Read the actual diff hunks for the config-like files; ignore pure comment, formatting, or test-only changes and say why.

If nothing triggers it, report "eval not required" with the file list and stop.

## 3. If eval-required, check the evidence
1. **A report exists in this branch:** a new file in `eval/reports/` (`YYYY-MM-DD-<git-sha>.md`) added by the diff. Its date and sha should match a commit on this branch, not an older one.
2. **It is in the PR description:** the "Eval report" section of the PR body (or the local PR draft) links or pastes it.
3. **It covers the change:** the report's model and prompt version match what the diff sets.
4. **Regression gate**, using the report and the previous report in `eval/reports/`:
   - False clear rate is exactly 0% on the golden set. Any false clear fails the gate.
   - No critical field (`supplier`, `invoice_number`, `invoice_date`, `total`, `currency`) dropped by more than 2 points versus the previous report, for any `doc_quality` split (clean, scanned, photo).
   - Accuracy is reported per field and per document type, not as one headline number.
   - Exception recall/precision did not silently fall.
5. **The golden set is untouched:** no changes under `data/golden/` unless the PR says why.

## 4. Report
Output a short table: trigger files, each check as PASS / FAIL / MISSING with the evidence (file, number, or quote). End with one verdict: **Gate passed**, **Gate failed** (state exactly what to fix), or **Eval not required**. If a report is missing, tell the user to run `make eval` themselves (it costs money) and add the result to the PR description.
