#!/usr/bin/env bash
# PreToolUse (Bash, only for `make eval*`): full eval makes real model calls and costs money.
# Forces a confirmation prompt (playbook §11.3: "ask first").
cat <<'JSON'
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"`make eval` runs the full golden set with real model calls and costs money. Confirm before running."}}
JSON
