# 0012: Accounting handoff creates draft bills, with an unknown state instead of blind retries

Status: accepted (Project B, 2026-10-27)

## Context

Project B hands approved invoices to an accounting system. The playbook listed real accounting connections
as out of scope for Demo A and planned only a CSV/JSON `Connector` interface (M12). The owner approved Project B
as an extension of this codebase. The accounting system and our database cannot share a transaction, so a
timeout can mean "created" or "not created".

## Decisions

- **Draft only, structurally.** The interface has no approve, pay or void method; the Xero adapter's only write
  is `PUT /Invoices` with `Status: DRAFT`; a source-level test fails if that changes.
- **Handoff state is its own table** (`accounting_handoffs`), not an invoice status. The invoice stays
  `approved`; Project A's and Project C's status semantics are untouched. (The playbook's `exported` is left for M12.)
- **Claim, then write once.** The claim is committed under a lock on the invoice before any provider call; the
  attempt is recorded before the write.
- **`unknown` is a real state.** After a possible write, nothing is retried; reconciliation or a person decides.
  Only failures the provider cannot have acted on (no connection, HTTP 429) are retried, three times at most.
- **AUD and NZD** are added to the supported currencies (two decimals). This is a change to a shared rule; it only
  adds currencies that were previously rejected. It is only half the job: a printed `$`, `A$` or `NZ$` is still
  not read as AUD or NZD (a bare `$` means USD only), so most Australian and New Zealand invoices still go to a
  person. Found when the AUD/NZD golden supplement was built; fixing it changes a rule and needs an evaluation.
- **No automatic contact creation, no expense account, no tax guessing** in this version.
- **Tokens** are Fernet-encrypted with their own key and refreshed under a row lock; one connection per tenant
  because refresh tokens rotate.
- **httpx** moves from a dev to a runtime dependency (BSD-3-Clause, no cost, already used by the test client).
- Any signed-in reviewer may trigger a handoff; there are no roles yet.

## Consequences

- A lost response costs a person a look in the accounting system; it can never cost a duplicate bill.
- The mock provider and recorded-response tests prove our logic, not Xero's behaviour. The first real run must be
  against the Xero Demo Company, and the unverified points in `docs/connectors.md` must be checked then.
- `make eval` was not run: no prompt, model, threshold or validation rule changed. The currency addition changes
  which invoices can be read as AUD/NZD; run the evaluation before relying on accuracy numbers for those.
