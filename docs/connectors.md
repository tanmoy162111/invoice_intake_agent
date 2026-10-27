# Accounting connectors

> **Creates draft bills only. A human must review and approve bills in the accounting platform.**

This system can hand an invoice that a reviewer has **approved** to an accounting system as a **draft
supplier bill**. It never approves a bill, never pays one, and never moves money. Approving and paying stay
with a person, inside the accounting platform. (Playbook: payments are permanently out of scope.)

## What is built, and what is proven

| Piece | State |
|---|---|
| Provider-neutral interface (`connectors/accounting.py`) | Built. Four methods: find contacts, get a contact, find bills, create a draft bill. There is deliberately no approve, pay or void method. |
| Mock provider (`connectors/mock.py`) | Built. In memory, offline. It can lose a response, refuse, rate-limit or crash, so the safety rules are tested against failures. **It proves nothing about any real provider.** |
| Xero adapter (`connectors/xero.py`) | Built and contract-tested against **recorded, Xero-shaped responses** (no network). **Never run against a real Xero organisation or the Xero Demo Company.** |
| MYOB | Not built. An extension point only: implement the interface in `connectors/myob.py`. |

Do not read "Xero adapter built" as "Xero integration verified". The first real check is the sandbox run
described below, and it has not been done.

## How a handoff stays safe

1. **Only approved invoices.** The invoice row is locked and its status is read; anything but `approved` is
   refused, and the check is repeated right before the write. Model confidence is never an approval.
2. **One handoff per invoice, provider and organisation.** `accounting_handoffs` has a unique constraint
   on that triple and a unique `handoff_key`. Double clicks, retries and two workers all land on the same row.
3. **Claim, look, record, write once, record.** The claim is committed before anything else happens. The contact
   lookup and the mapping write nothing. The attempt is recorded *before* the write is made.
4. **A timeout is not a failure.** If the provider may have received the request (timeout, dropped connection,
   5xx, unreadable answer, conflict), the state is `unknown`. Nothing is written again. Only **reconciliation**
   (find exactly one matching draft bill created by this application) or **a person** (who checked the
   accounting system) can move it on. Finding nothing proves nothing, so it stays `unknown`.
5. **A crashed worker is the same.** A handoff `in_progress` for 15 minutes becomes `unknown`
   (`WORKER_INTERRUPTED`), never `failed`.
6. **Success is evidence.** `succeeded` is recorded only when the answer is a bill in `DRAFT` status with the
   contact, currency and totals that were sent. Any other status is critical: it is recorded and left alone, with no
   corrective approve or pay call.
7. **Nothing is guessed.** A missing invoice number or date, an unsupported currency, lines that do not add up to
   the totals, tax the provider would calculate differently, a tax rate with no approved mapping, an ambiguous or
   missing contact: each stops the handoff before any write, with a plain sentence. Amounts are never adjusted.
   The expense account is left blank.
8. **Audit.** Every step writes an append-only audit event (codes and ids; never names, amounts or tokens). If
   the audit write fails after the provider answered, the handoff stays `in_progress`, becomes `unknown`
   later, and is reconciled. A failure is never hidden.

The audit table is append-only through the application and protected by a database trigger. It is not
cryptographically tamper-evident, and this project does not claim it is.

## Contact matching

In order: a person-confirmed link (`supplier_accounting_links`), then the tax number (ABN/NZBN, Xero's
`TaxNumber`), then an exact name (case and punctuation ignored). Several candidates, none, or a tax number that
disagrees all stop the handoff for a person. A contact is never created automatically in this version.

## Setting up Xero (not yet done)

This needs a Xero developer account and an app: **an account and terms that someone has to accept**. Nothing in
this repository does that for you.

1. Create an app at <https://developer.xero.com> (authorization code flow with PKCE; redirect URI = a page of
   the web app that forwards `code` and `state` to `POST /accounting/xero/callback`; HTTPS when deployed).
2. Set in `.env`: `ACCOUNTING_PROVIDER=xero`, `ACCOUNTING_ENCRYPTION_KEY` (a Fernet key), `XERO_CLIENT_ID`,
   `XERO_CLIENT_SECRET` (never commit it), `XERO_REDIRECT_URI`, `XERO_SCOPES` (what the app was granted), and
   `ACCOUNTING_TAX_TYPES`, for example `{"1000": "INPUT"}`: your tax rate in basis points to **your
   organisation's** tax type. Look the types up in the organisation; the example is not a recommendation.
3. Sign in to the review app, call `POST /accounting/xero/connect`, open the returned address, approve, and
   forward the result to `POST /accounting/xero/callback`. If the sign-in grants several organisations, set
   `XERO_ORGANISATION_ID` and connect again; nothing is stored until exactly one is chosen.
4. Use the **Xero Demo Company** for the first real run. Do not connect a client's live books.

### What the Xero adapter does and how it was checked

Checked against Xero's published Accounting API OpenAPI description (`XeroAPI/Xero-OpenAPI`, v19.1.0):
`PUT /Invoices` with `Type: ACCPAY`, `Status: DRAFT`, `LineAmountTypes: Exclusive`; `GET /Invoices` filters
`InvoiceNumbers`, `ContactIDs`, `createdByMyApp`; `GET /Contacts?searchTerm=`; the `xero-tenant-id` header;
`Idempotency-Key` (maximum 128 characters); authorize and token endpoints; `Contact.TaxNumber`.

**Not verified** (the developer portal pages could not be read as text; these need a real check):

- That a supplier's invoice number belongs in `InvoiceNumber` on a bill. The description of that field in the
  description file talks about sales invoices only. Reconciliation depends on it.
- What `Idempotency-Key` does on a replay (retention, conflicting payloads). It is sent as a second line of
  defence and never relied on.
- Refresh-token rotation and reuse rules (the token store assumes the safe case: a refresh replaces the token, and
  a lock stops two workers refreshing together).
- The scope names for your app. Web search results say apps created on or after 2 March 2026 get granular
  scopes (`accounting.invoices`) and older apps may keep broad ones until about September 2027. Set `XERO_SCOPES`
  to what the app was actually granted.
- Rate limits, the Starter tier's five-connection limit, whether the Demo Company counts toward it, and pricing
  (search results show a free Starter tier; paid tiers from about AUD 35 a month). Check the developer
  dashboard before relying on any of it.
- Tax types and account codes, which are organisation-specific.

## Adding another provider (for example MYOB)

1. Implement `AccountingConnector` (`connectors/accounting.py`). Raise `ProviderError` with the right `Failure`
   so the service knows whether the request can have been acted on: `CONNECT_FAILED` and `RATE_LIMITED` only if
   it cannot have been.
2. Create bills in the provider's draft/unapproved state, never in a state a payment run can pick up. If the
   provider cannot guarantee that, do not enable writes.
3. Return `ReturnedBill` in integer minor units and the provider's status string.
4. Read the provider's documentation for authentication, scopes, rate limits and the bill model; do not copy
   the Xero assumptions above.
5. Add contract tests with recorded responses (see `tests/unit/test_xero_connector.py`) and add the provider to
   `ACCOUNTING_PROVIDER` and `handoff/factory.py`.

## Tests

`tests/unit/test_accounting.py` (rules), `test_oauth.py`, `test_xero_connector.py` (contract),
`test_handoff_safety.py` (source guards: no approve or pay path, no secrets in the template),
`tests/integration/test_handoff_service.py` (database, failure injection, concurrency),
`test_handoff_api.py` (HTTP, connect flow, end-to-end with recorded Xero responses) and
`test_accounting_tokens.py` (encryption and the refresh lock).

## Troubleshooting

See the codes in the manual (section 4.17). Short version: `unknown` means *look in the accounting system, then
use "Look for the bill" or record what you found*; `failed` is safe to try again once the cause is fixed.
