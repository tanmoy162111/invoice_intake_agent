# Exception taxonomy

Single source of truth in code: `apps/api/src/intake/core/exceptions.py` (one enum, with an
explanation template and suggested fix per code). The table below is generated from it and a
unit test fails if they drift. Every code also needs a test (`tests/unit/test_exceptions.py`).

Severity:
- `info`: shown, but does not stop the invoice.
- `review`: needs a person.
- `block`: needs a person **and** an explicit resolution note before approval.

| Code | Severity | Example explanation | Suggested fix |
|---|---|---|---|
| `UNREADABLE_DOCUMENT` | block | The file couldn't be read clearly (page 2 is blank or too blurry). | Ask the supplier for a clearer copy |
| `LOW_CONFIDENCE_FIELD` | review | We're not sure about the total: it wasn't found in the document's text layer. | Check the highlighted field and confirm or correct it |
| `LINE_MATH_MISMATCH` | review | Line 3: 12 × USD 45.00 should be USD 540.00, but the invoice says USD 504.00. | Confirm with the supplier; possibly a typo |
| `TOTAL_MISMATCH` | review | The lines add up to USD 2,140.00, but the invoice total is USD 2,410.00. | Ask the supplier for a corrected invoice |
| `TAX_MISMATCH` | review | Tax is USD 318.00, but 15% of USD 2,140.00 is USD 321.00. | Check the tax rate for this supplier |
| `INVALID_DATE` | review | The invoice date is invalid: it is 3 months in the future. | Confirm the date with the supplier |
| `UNKNOWN_SUPPLIER` | review | 'Acme Supplies Ltd' doesn't match any known supplier. Closest match: 'ACME Supply Co.' (82%). | Link to an existing supplier or create a new one |
| `BANK_DETAILS_CHANGED` | block | The bank account on this invoice is different from the one on file for Acme Supply Co.. | Verify by phone using a known contact before approving. This is a common fraud pattern |
| `POSSIBLE_DUPLICATE` | block | This looks like invoice INV-1043 from the same supplier: the same amount and a very similar number, with invoice dates 4 days apart. | Compare them side by side; reject if duplicate |
| `NO_PO` | review | This invoice has no PO number, and no open PO for this supplier matches the amount. | Ask the requester for the PO, or approve as a non-PO invoice |
| `PO_NOT_FOUND` | review | PO-7781 isn't in the system. | Check for a typo or ask purchasing |
| `PRICE_VARIANCE` | review | Line 2 is billed at USD 48.00 per unit, but the PO says USD 45.00 (+6.7%, limit 2%). | Accept the variance, or ask the supplier to correct it |
| `QTY_VARIANCE` | review | Line 1 bills 120 units, but the PO line is for 100. | Ask the supplier or purchasing |
| `RECEIPT_MISSING` | review | Nothing has been received yet for PO-7781. | Wait for the goods receipt or confirm delivery with the requester |
| `QTY_NOT_RECEIVED` | review | Line 1 bills 100 units, but only 80 have been received. | Hold, or pay partially once the rest arrives |
| `PO_OVERBILLED` | block | Invoices against PO-7781 now total USD 10,450.00, which is more than the PO total of USD 9,800.00. | Stop and check with purchasing |
| `CURRENCY_MISMATCH` | review | The invoice is in EUR, but the PO is in USD. | Confirm the currency with the supplier |
| `ABOVE_APPROVAL_LIMIT` | review | The total of USD 25,000.00 is above the USD 10,000.00 auto-approval limit. | Needs manager approval |

To add a code, use the `add-exception-code` skill: enum + spec + test + this table + a planted
example in the synthetic dataset.
