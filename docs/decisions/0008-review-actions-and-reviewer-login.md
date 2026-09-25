# 0008 — Review actions: login, corrections that re-run the checks, and final decisions
Date: 2026-09-26 · Status: accepted

## Context
Playbook M8 asks for a reviewer to correct a field (which re-runs the checks), resolve or dismiss exceptions
(a note for `block`), approve, reject, request information, and see bank details masked (revealing logged),
behind a simple demo login. M8 was split: M8a the API and rules, M8b the screen. The playbook status diagram
has no way for an invoice to return to the checks, and the API had only an interim static token.

## Decision
1. **Login.** One demo reviewer (`REVIEWER_USERNAME`, `REVIEWER_PASSWORD`). `POST /auth/login` returns a signed
   session token (`v1.<payload>.<HMAC-SHA256>`, key `SESSION_SECRET`, lifetime `SESSION_TTL_S`, default 8
   hours). No password or secret configured means nobody can log in. Five failed attempts in a minute lock the
   login for that minute (in memory, per process). `/auth/login` is the only public route besides `/health`.
2. **Two kinds of caller.** The static `API_TOKEN` is for scripts: it can upload, read the queue and read
   invoices. Every action needs a reviewer session (`require_user`), so the history always names a person. The
   upload guard accepts either.
3. **Approval** needs every exception resolved or dismissed (both severities), and the invoice `cleared` or
   `needs_review`. Closing a `block` exception needs a written note. Notes are at most 2,000 characters.
   Approve and reject are final: no correction, decision or exception change afterwards.
4. **Corrections** are header fields only (never the bank account, never lines in M8a). The value is
   normalized by the same parsers that read a document, stored in `field_extractions.corrected_value` beside
   the original with the person's name, and the invoice column is updated. Blanking is allowed only for optional
   fields. The bank account is not editable because its hash and encryption must come from a read, not typing.
5. **A correction re-runs everything.** Open exceptions are closed by the system ("Re-checked after a
   correction"), the invoice's check results, line matches, supplier link and route are cleared, it returns to
   `extracted` (new edge), and validation, duplicates, the match and routing run again in the same transaction
   without waiting for other invoices. An exception a person already closed, with the same code and the same
   explanation (so the same numbers), is not raised again. One closed by the system, or still open, never is.
6. **Status flow.** Two edges are added to playbook 5.2: `needs_review` and `cleared` to `extracted` (re-check),
   and `cleared` to `rejected`. A test pins the edge set.
7. **Request information** is recorded (`review_actions`, audit `info_requested`) and changes no status: there is
   no "waiting" status, and nothing is sent to the supplier.
8. **Bank details** are shown masked (last four characters). Revealing one is a reviewer-only action that
   writes a `bank_details_revealed` audit event (never the account).
9. **What is recorded.** Every action is a `review_actions` row (the decision and its note, the correction's
   before and after) and audit events with field names and codes only. The `resolve` action is recorded as
   `dismiss_exception` with `resolution: resolved`, because the action vocabulary is fixed by the schema.
10. **Concurrency.** The review service locks the invoice row for the whole action, as the routing stage does.

## Why
A decision that does not name a person, an approval that skips an unread warning, or a correction that does not
re-check would each defeat the purpose of the queue. Keeping the rules pure and the service thin keeps them
testable without a browser.

## Consequences
- Later invoices matched after a corrected one are not re-run; their cumulative billing keeps the old numbers
  until they are corrected too.
- A shared demo identity is not an access control system; it must be replaced before real data.
- The rate limit is per process and resets on restart.
- Line corrections, a waiting status and sending requests to suppliers are future work.
