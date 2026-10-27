"""What any accounting system must offer for the draft-bill hand-off (Project B).

Creates draft bills only. A human must review and approve bills in the accounting platform.

The interface has no way to approve, pay or change the state of a bill, on purpose: a provider
adapter cannot do what the interface cannot say. Adding a provider means implementing this class
(see docs/connectors.md); the pipeline and the hand-off service do not change.
"""

from typing import Protocol

from intake.core.accounting import BillDraft, ContactCandidate, Failure, ReturnedBill


class ProviderError(Exception):
    """A call to the accounting system failed. `failure` says how, so the service knows whether the
    request can have been acted on. `detail` is safe to store: never a token, header or body."""

    def __init__(
        self, failure: Failure, *, retry_after_s: int | None = None, detail: str = ""
    ) -> None:
        super().__init__(f"{failure.value}: {detail}" if detail else failure.value)
        self.failure = failure
        self.retry_after_s = retry_after_s
        self.detail = detail


class AccountingConnector(Protocol):
    provider: str
    organisation_id: str

    def find_contacts(self, name: str, tax_id: str | None) -> list[ContactCandidate]:
        """Contacts that might be this supplier (read only). The rules choose; this only lists."""
        ...

    def get_contact(self, contact_id: str) -> ContactCandidate | None:
        """One contact by its provider id, or None if it no longer exists (read only)."""
        ...

    def find_bills(
        self, contact_id: str, invoice_number: str, *, created_by_us: bool
    ) -> list[ReturnedBill]:
        """Bills of this contact with this supplier invoice number (read only). `created_by_us`
        limits the answer to bills this application created, which is what reconciliation needs."""
        ...

    def create_draft_bill(self, bill: BillDraft, idempotency_key: str) -> ReturnedBill:
        """Create one bill in Draft status. Raises ProviderError. Never approves or pays."""
        ...
