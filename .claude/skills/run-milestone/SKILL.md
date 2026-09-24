---
name: run-milestone
description: Work one playbook milestone (M0-M13) using the team loop - read the spec, propose a plan and wait for approval, build in small steps with core/ tests first, run make check, and prepare the PR description.
disable-model-invocation: true
argument-hint: <milestone, e.g. M3>
---

# Run milestone $ARGUMENTS

Follow `demo-a-developer-playbook.md` §9 (milestone), §11.1 and §11.2, plus `CLAUDE.md`. One milestone at a time; stay inside its scope.

## 1. Understand
- Read the section for `$ARGUMENTS` in playbook §9 (goal, tasks, acceptance criteria) and every section it references (data model §5, pipeline §6, taxonomy §7, eval §8, security §10).
- Read `CLAUDE.md`. If a GitHub issue was pasted or is linked, read it too.
- Check that earlier milestones' acceptance criteria are met. If not, stop and tell the user; do not start a later milestone early.
- Inspect the repo as it currently exists so the plan fits real files, not the ideal layout.

## 2. Plan first, then wait
Propose a plan and **stop for approval before writing any code**:
- Branch name (`feat/mN-short-name`).
- Files to create or change.
- Tests to write (unit tests for `core/`, integration tests with recorded LLM responses, Playwright for UI).
- Docs to update in the same PR.
- Whether prompts, models, thresholds or rules change (which requires `make eval` and a report; ask before running it, it costs money).
- Open questions or anything in the playbook that looks unclear or wrong. Ask rather than guess (playbook §0).

## 3. Build in small steps
- Work on the milestone branch. Do not fix unrelated problems; list them for the PR instead.
- For logic in `core/`, write the failing tests first, then the code (pure functions only).
- Money is integer minor units plus currency. Every state change writes an audit event. Never touch `audit_events` with UPDATE/DELETE. Never call the model API in unit or CI tests.
- Use the `core-purity-reviewer` agent after changing `core/`, and the `security-reviewer` agent after touching uploads, bank details, auth, logging, or the LLM client.
- Check Anthropic SDK details in the current docs; never guess.

## 4. Verify
- Run `make check` and report the real output. If a command doesn't exist yet, say so.
- Walk through each acceptance criterion for `$ARGUMENTS` and state whether it is met, with evidence (test name, command output, or screenshot). Do not claim a criterion is met without running it.

## 5. Prepare the PR
Draft the PR description using the template in playbook §14: What changed, Why (link the issue), How it was tested, Screenshots (UI), Eval report (if required), Docs updated. Use conventional commit messages. Do not open, merge, or push without the user asking.
