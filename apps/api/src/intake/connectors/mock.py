"""An in-memory accounting system for tests and the offline demo. It behaves like a provider that
can go wrong: it can refuse, be rate limited, or create a bill and then lose the answer. It is not
evidence that any real provider works; see docs/connectors.md for what has been verified."""

import time
from dataclasses import dataclass, field

from intake.connectors.accounting import ProviderError
from intake.core.accounting import (
    DRAFT,
    BillDraft,
    ContactCandidate,
    Failure,
    ReturnedBill,
)


@dataclass
class MockAccounting:
    contacts: list[ContactCandidate] = field(default_factory=list)
    echo_contacts: bool = False  # the offline demo: every supplier has exactly one contact
    provider: str = "mock"
    organisation_id: str = "mock-org"
    bills: list[tuple[ReturnedBill, str, bool]] = field(default_factory=list)  # bill, number, ours
    create_calls: int = 0
    keys_seen: list[str] = field(default_factory=list)
    _before: list[Failure] = field(default_factory=list)
    _after: list[Failure] = field(default_factory=list)
    _status: str = DRAFT
    _retry_after: int | None = None
    _delay: float = 0.0

    # ---- test controls -------------------------------------------------------------------------
    def fail_before_processing(self, failure: Failure, *, retry_after_s: int | None = None) -> None:
        """The next create is refused without being acted on (connect failure, 429, 401...)."""
        self._before.append(failure)
        self._retry_after = retry_after_s

    def lose_response(self, failure: Failure = Failure.TIMEOUT) -> None:
        """The next create IS carried out, then the caller is told it failed."""
        self._after.append(failure)

    def create_with_status(self, status: str) -> None:
        self._status = status

    def slow_create(self, seconds: float) -> None:
        self._delay = seconds

    def add_existing_bill(self, contact_id: str, number: str, total_minor: int = 0) -> None:
        bill = ReturnedBill(
            f"manual-{len(self.bills) + 1}", DRAFT, contact_id, "AUD", 0, 0, total_minor
        )
        self.bills.append((bill, number, False))

    # ---- the connector interface ----------------------------------------------------------------
    def find_contacts(self, name: str, tax_id: str | None) -> list[ContactCandidate]:
        if self.echo_contacts:
            return [ContactCandidate(f"demo-{name}", name, tax_id)]
        return list(self.contacts)

    def get_contact(self, contact_id: str) -> ContactCandidate | None:
        if self.echo_contacts:
            return ContactCandidate(contact_id, contact_id.removeprefix("demo-"), None)
        return next((c for c in self.contacts if c.contact_id == contact_id), None)

    def find_bills(
        self, contact_id: str, invoice_number: str, *, created_by_us: bool
    ) -> list[ReturnedBill]:
        return [
            b
            for b, number, ours in self.bills
            if b.contact_id == contact_id
            and number == invoice_number
            and (ours or not created_by_us)
        ]

    def create_draft_bill(self, bill: BillDraft, idempotency_key: str) -> ReturnedBill:
        self.create_calls += 1
        self.keys_seen.append(idempotency_key)
        if self._delay:
            time.sleep(self._delay)
        if self._before:
            raise ProviderError(self._before.pop(0), retry_after_s=self._retry_after, detail="mock")
        # Deliberately no idempotency-key replay here: a second create call makes a second bill,
        # so a test fails if the service ever writes twice.
        made = ReturnedBill(
            bill_id=f"bill-{len(self.bills) + 1}", status=self._status,
            contact_id=bill.contact_id, currency=bill.currency, subtotal_minor=bill.subtotal_minor,
            tax_minor=bill.tax_minor, total_minor=bill.total_minor,
        )  # fmt: skip
        self.bills.append((made, bill.invoice_number, True))
        if self._after:
            raise ProviderError(self._after.pop(0), detail="mock: response lost")
        return made
