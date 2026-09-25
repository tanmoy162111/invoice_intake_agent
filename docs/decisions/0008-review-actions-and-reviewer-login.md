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
   hours). No password or secret configured means nobody can log in. Attempts are limited to 5 a minute per
   client address and 50 overall, reserved atomically under a lock before the password is checked (a good login
   gives its client's attempts back), with `Retry-After`. The client is the connection's address, never a
   forwarded-for header. State is in memory, per process. Sign-ins and failures are audit events (the name typed
   is not recorded). `/auth/login` is the only public route besides `/health`. A session is honoured only while
   its user equals `REVIEWER_USERNAME` and a password is configured, so switching login off ends it. Password
   (8) and secret (16) have minimum lengths, and the name may not be `system`.
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
   A value may not contain control, invisible or direction characters, must contain a letter or digit (text
   fields), amounts are at most 10^15 minor units and dates are in 2000 to 2100. The currency can be changed only
   while no amounts were read: amounts cannot be re-read here, and rescaling them silently (cents to yen) would
   be wrong, so an invoice with a wrong currency and amounts is rejected and a corrected copy requested.
5. **A correction re-runs everything.** Open exceptions are closed by the system ("Re-checked after a
   correction"), the invoice's check results, line matches, supplier link and route are cleared, it returns to
   `extracted` (new edge), and validation, duplicates, the match and routing run again in the same transaction
   without waiting for other invoices. An exception a person already closed, with the same code and the same
   explanation (so the same numbers), is not raised again, **except** `BANK_DETAILS_CHANGED`: its words name no
   account, so a dismissal cannot be known to still apply and a person decides again every time. One closed by
   the system, or still open, never is carried over.
6. **Status flow.** Two edges are added to playbook 5.2: `needs_review` and `cleared` to `extracted` (re-check),
   and `cleared` to `rejected`. A test pins the edge set.
7. **Request information** is recorded (`review_actions`, audit `info_requested`) and changes no status: there is
   no "waiting" status, and nothing is sent to the supplier.
8. **Bank details** are shown masked (last four characters). Revealing one is a reviewer-only action that
   writes a `bank_details_revealed` audit event (never the account).
9. **What is recorded.** Every action is a `review_actions` row (the decision and its note, the correction's
   before and after) and audit events with field names and codes only. The `resolve` action is recorded as
   `dismiss_exception` with `resolution: resolved`, because the action vocabulary is fixed by the schema.
10. **Concurrency.** The review service locks the invoice row for the whole action, as the routing stage does,
    and reads an exception only after taking that lock (it is refreshed, so a second reviewer who was waiting sees
    that it is already closed).
11. **Notes** are stored without control or invisible characters, and a note that is required (a block
    exception, a rejection) must contain at least a letter or digit.
12. **Bank reveal** is never cached; a value that cannot be decrypted with the configured key is a 409
    `BANK_UNREADABLE` and the attempt is logged. Page images are never cached either.

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
