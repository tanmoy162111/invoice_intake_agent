"""Project B: handing an approved invoice to an accounting system as a draft bill, against a real
database and a mock provider that can fail in the ways real ones do. The point of every test here
is the same: the provider is asked to create a bill at most once, and never for an unapproved
invoice."""

import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from intake.connectors.mock import MockAccounting
from intake.core.accounting import STALE_AFTER_S, ContactCandidate, Failure, Problem
from intake.core.statuses import HandoffStatus, InvoiceStatus
from intake.db.models import (
    AccountingHandoff,
    AuditEvent,
    Document,
    Invoice,
    InvoiceLine,
    Supplier,
    SupplierAccountingLink,
    Tenant,
)
from intake.handoff.service import (
    HandoffRefused,
    NotFound,
    reconcile,
    recover_stale,
    request_handoff,
    resolve_unknown,
)

TAX = {1000: "INPUT"}
ABN = "51824753556"


class Case:
    def __init__(self, engine: Engine, status: InvoiceStatus = InvoiceStatus.APPROVED) -> None:
        self.engine = engine
        with Session(engine) as s:
            tenant = Tenant(name=f"t-{uuid.uuid4().hex[:6]}")
            s.add(tenant)
            s.flush()
            sup = Supplier(
                tenant_id=tenant.id, name="Acme Pty Ltd", tax_id=ABN, default_currency="AUD"
            )
            doc = Document(
                tenant_id=tenant.id, file_sha256=uuid.uuid4().hex + uuid.uuid4().hex[:32],
                filename="a.pdf", mime="application/pdf", storage_path="x", source="test",
            )  # fmt: skip
            s.add_all([sup, doc])
            s.flush()
            inv = Invoice(
                tenant_id=tenant.id, document_id=doc.id, status=status.value, supplier_id=sup.id,
                supplier_name="Acme Pty Ltd", invoice_number="INV-001",
                invoice_date=date(2026, 3, 1), due_date=date(2026, 3, 31), subtotal_minor=10000,
                tax_minor=1000, total_minor=11000, currency="AUD",
            )  # fmt: skip
            s.add(inv)
            s.flush()
            s.add(
                InvoiceLine(
                    tenant_id=tenant.id,
                    invoice_id=inv.id,
                    line_no=1,
                    description="Paper",
                    qty=Decimal("2"),
                    unit_price_minor=5000,
                    amount_minor=10000,
                    tax_rate=Decimal("10"),
                )  # fmt: skip
            )
            s.commit()
            self.tenant, self.invoice, self.supplier = tenant.id, inv.id, sup.id
        self.mock = MockAccounting(contacts=[ContactCandidate("c-1", "Acme Pty Ltd", ABN)])
        self.slept: list[float] = []

    def run(self, actor: str = "reviewer", mock: MockAccounting | None = None) -> object:
        with Session(self.engine) as s:
            return request_handoff(
                s, connector=mock or self.mock, tax_types=TAX, tenant_id=self.tenant,
                invoice_id=self.invoice, actor=actor, sleep=self.slept.append,
            )  # fmt: skip

    def row(self) -> AccountingHandoff:
        with Session(self.engine) as s:
            h = s.scalars(
                select(AccountingHandoff).where(AccountingHandoff.invoice_id == self.invoice)
            ).one()
            s.expunge(h)
            return h

    def row_count(self) -> int:
        with Session(self.engine) as s:
            return len(
                s.scalars(
                    select(AccountingHandoff).where(AccountingHandoff.invoice_id == self.invoice)
                ).all()
            )

    def tenant_events(self) -> list[str]:
        with Session(self.engine) as s:
            return list(
                s.scalars(select(AuditEvent.event_type).where(AuditEvent.tenant_id == self.tenant))
            )

    def events(self) -> list[str]:
        with Session(self.engine) as s:
            return list(
                s.scalars(
                    select(AuditEvent.event_type)
                    .where(AuditEvent.invoice_id == self.invoice)
                    .order_by(AuditEvent.id)
                )
            )

    def event_data(self) -> list[dict[str, object]]:
        with Session(self.engine) as s:
            return list(
                s.scalars(select(AuditEvent.data).where(AuditEvent.invoice_id == self.invoice))
            )

    def age_handoff(self, seconds: int) -> None:
        with Session(self.engine) as s:
            h = s.scalars(
                select(AccountingHandoff).where(AccountingHandoff.invoice_id == self.invoice)
            ).one()
            h.started_at = datetime.now(UTC) - timedelta(seconds=seconds)
            s.commit()


@pytest.fixture
def case(engine: Engine) -> Case:
    return Case(engine)


# ---- the good path --------------------------------------------------------------------------


def test_an_approved_invoice_becomes_one_draft_bill(case: Case) -> None:
    out = case.run()
    row = case.row()
    assert (row.status, row.external_bill_id, row.manual_action) == ("succeeded", "bill-1", False)
    assert (row.provider, row.organisation_id, row.contact_id) == ("mock", "mock-org", "c-1")
    assert row.requested_by == "reviewer" and row.started_at and row.finished_at
    assert case.mock.create_calls == 1
    [(bill, number, ours)] = case.mock.bills
    assert (bill.status, number, ours) == ("DRAFT", "INV-001", True)
    assert out.status is HandoffStatus.SUCCEEDED


def test_the_whole_lifecycle_is_audited_without_values_or_secrets(case: Case) -> None:
    case.run()
    events = case.events()
    for expected in (
        "handoff_requested",
        "mapping_passed",
        "draft_bill_attempted",
        "draft_bill_created",
    ):
        assert expected in events
    assert events.index("draft_bill_attempted") < events.index("draft_bill_created")
    blob = repr(case.event_data()).lower()
    for forbidden in ("acme", "inv-001", "token", "secret", "11000", ABN):
        assert forbidden.lower() not in blob


def test_a_stored_contact_link_is_used_before_searching(case: Case) -> None:
    case.mock.contacts.append(
        ContactCandidate("c-2", "Acme Pty Ltd", None)
    )  # now ambiguous by name
    case.mock.contacts[0] = ContactCandidate("c-1", "Something Else", None)
    with Session(case.engine) as s:
        s.add(
            SupplierAccountingLink(
                tenant_id=case.tenant,
                supplier_id=case.supplier,
                provider="mock",
                organisation_id="mock-org",
                contact_id="c-2",
            )  # fmt: skip
        )
        s.commit()
    case.run()
    assert case.row().contact_id == "c-2" and case.row().status == "succeeded"


# ---- only approved invoices -----------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [s for s in InvoiceStatus if s is not InvoiceStatus.APPROVED],
)
def test_an_invoice_that_is_not_approved_never_reaches_the_provider(
    engine: Engine, status: InvoiceStatus
) -> None:
    c = Case(engine, status)
    with pytest.raises(HandoffRefused) as err:
        c.run()
    assert err.value.problem is Problem.NOT_APPROVED
    assert c.mock.create_calls == 0
    assert "handoff_refused" in c.events()
    with Session(engine) as s:
        assert (
            s.scalars(
                select(AccountingHandoff).where(AccountingHandoff.invoice_id == c.invoice)
            ).first()
            is None
        )


def test_another_tenants_invoice_is_not_found(case: Case, engine: Engine) -> None:
    with Session(engine) as s, pytest.raises(NotFound):
        request_handoff(
            s, connector=case.mock, tax_types=TAX, tenant_id=uuid.uuid4(),
            invoice_id=case.invoice, actor="reviewer",
        )  # fmt: skip
    assert case.mock.create_calls == 0


def test_an_invoice_that_stopped_being_approved_before_the_write_is_not_written(
    case: Case, engine: Engine
) -> None:
    """Approval is checked again right before the write, not only when the request arrived."""

    class Flipping(MockAccounting):
        def find_bills(self, *a: object, **k: object) -> list:  # type: ignore[override]
            with Session(engine) as s:
                inv = s.get(Invoice, case.invoice)
                assert inv is not None
                inv.status = InvoiceStatus.EXPORTED.value
                s.commit()
            return []

    m = Flipping(contacts=case.mock.contacts)
    case.run(mock=m)
    assert m.create_calls == 0
    assert case.row().status == "failed" and case.row().failure_code == "NOT_APPROVED"


# ---- repeated and concurrent requests -------------------------------------------------------


def test_asking_again_after_success_creates_no_second_bill(case: Case) -> None:
    case.run()
    with pytest.raises(HandoffRefused) as err:
        case.run()
    assert err.value.problem is Problem.ALREADY_HANDED_OFF
    assert case.mock.create_calls == 1 and len(case.mock.bills) == 1


def test_two_requests_at_the_same_moment_create_one_bill(case: Case) -> None:
    case.mock.slow_create(0.4)
    outcomes: list[str] = []
    start = threading.Barrier(2)

    def go() -> None:
        start.wait()
        try:
            case.run()
            outcomes.append("ok")
        except HandoffRefused as e:
            outcomes.append(e.problem.value)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) in (
        ["ALREADY_HANDED_OFF", "ok"],
        ["HANDOFF_IN_PROGRESS", "ok"],
    )
    assert case.mock.create_calls == 1 and len(case.mock.bills) == 1
    assert case.row().status == "succeeded"


# ---- when the provider answers badly --------------------------------------------------------


def test_a_lost_response_is_an_unknown_outcome_and_blocks_a_second_write(case: Case) -> None:
    case.mock.lose_response(Failure.TIMEOUT)
    out = case.run()
    row = case.row()
    assert (row.status, row.manual_action, row.failure_code) == ("unknown", True, "timeout")
    assert len(case.mock.bills) == 1  # the provider did create it
    assert out.status is HandoffStatus.UNKNOWN
    with pytest.raises(HandoffRefused) as err:
        case.run()
    assert err.value.problem is Problem.OUTCOME_UNKNOWN
    assert case.mock.create_calls == 1


def test_reconciliation_finds_the_bill_that_was_created_and_records_it(
    case: Case, engine: Engine
) -> None:
    case.mock.lose_response()
    case.run()
    with Session(engine) as s:
        out = reconcile(
            s, connector=case.mock, tax_types=TAX, tenant_id=case.tenant,
            invoice_id=case.invoice, actor="system",
        )  # fmt: skip
    assert out.status is HandoffStatus.SUCCEEDED and out.external_bill_id == "bill-1"
    assert case.mock.create_calls == 1
    assert "handoff_reconciled" in case.events()


def test_reconciliation_that_finds_nothing_proves_nothing(case: Case, engine: Engine) -> None:
    case.mock.fail_before_processing(Failure.TIMEOUT)  # timed out, and nothing was created
    case.run()
    with Session(engine) as s:
        out = reconcile(
            s, connector=case.mock, tax_types=TAX, tenant_id=case.tenant,
            invoice_id=case.invoice, actor="system",
        )  # fmt: skip
    assert out.status is HandoffStatus.UNKNOWN and out.manual_action
    assert "reconciliation_inconclusive" in case.events()
    with pytest.raises(HandoffRefused):  # still no blind second write
        case.run()
    assert case.mock.create_calls == 1


def test_a_person_confirming_nothing_exists_allows_one_new_attempt(
    case: Case, engine: Engine
) -> None:
    case.mock.fail_before_processing(Failure.TIMEOUT)
    case.run()
    with Session(engine) as s:
        resolve_unknown(
            s, provider="mock", organisation_id="mock-org", tenant_id=case.tenant,
            invoice_id=case.invoice, actor="reviewer", bill_id=None,
        )  # fmt: skip
    assert case.row().status == "failed"
    case.run()
    assert case.row().status == "succeeded" and case.mock.create_calls == 2
    assert len(case.mock.bills) == 1


def test_a_person_can_record_the_bill_they_found(case: Case, engine: Engine) -> None:
    case.mock.fail_before_processing(Failure.TIMEOUT)
    case.run()
    with Session(engine) as s:
        out = resolve_unknown(
            s, provider="mock", organisation_id="mock-org", tenant_id=case.tenant,
            invoice_id=case.invoice, actor="reviewer", bill_id=" BILL-77 ",
        )  # fmt: skip
    assert out.status is HandoffStatus.SUCCEEDED and out.external_bill_id == "BILL-77"
    assert "handoff_resolved_found" in case.events()


def test_only_an_unknown_handoff_can_be_resolved_or_reconciled(case: Case, engine: Engine) -> None:
    case.run()  # succeeded
    with Session(engine) as s, pytest.raises(NotFound):
        resolve_unknown(
            s, provider="mock", organisation_id="mock-org", tenant_id=case.tenant,
            invoice_id=case.invoice, actor="r", bill_id=None,
        )  # fmt: skip
    with Session(engine) as s, pytest.raises(NotFound):
        reconcile(
            s, connector=case.mock, tax_types=TAX, tenant_id=case.tenant,
            invoice_id=case.invoice, actor="r",
        )  # fmt: skip


@pytest.mark.parametrize(
    "failure", [Failure.SERVER_ERROR, Failure.CONNECTION_LOST, Failure.CONFLICT]
)
def test_failures_after_sending_are_unknown_never_retried(case: Case, failure: Failure) -> None:
    case.mock.lose_response(failure)
    case.run()
    assert case.row().status == "unknown" and case.mock.create_calls == 1 and case.slept == []


def test_a_crash_after_the_provider_wrote_is_recovered_without_a_second_bill(
    case: Case, engine: Engine
) -> None:
    class Crash(BaseException):
        """Stands in for the process being killed: nothing in the service may catch it."""

    class Dying(MockAccounting):
        def create_draft_bill(self, bill, key):  # type: ignore[no-untyped-def]
            super().create_draft_bill(bill, key)  # the bill is created...
            raise Crash  # ...and the worker dies before it can record anything

    dying = Dying(contacts=case.mock.contacts)
    with pytest.raises(Crash):
        case.run(mock=dying)
    assert case.row().status == "in_progress"
    with pytest.raises(HandoffRefused) as err:  # someone clicks again
        case.run(mock=dying)
    assert err.value.problem is Problem.HANDOFF_IN_PROGRESS
    case.age_handoff(STALE_AFTER_S + 5)
    with Session(engine) as s:
        assert recover_stale(s, tenant_id=case.tenant) == 1
    assert case.row().status == "unknown" and case.row().failure_code == "WORKER_INTERRUPTED"
    with Session(engine) as s:
        out = reconcile(
            s, connector=dying, tax_types=TAX, tenant_id=case.tenant,
            invoice_id=case.invoice, actor="system",
        )  # fmt: skip
    assert out.status is HandoffStatus.SUCCEEDED
    assert dying.create_calls == 1 and len(dying.bills) == 1


def test_a_stale_claim_is_noticed_when_the_next_request_arrives(case: Case) -> None:
    class Crash(BaseException):
        pass

    class Dying(MockAccounting):
        def create_draft_bill(self, bill, key):  # type: ignore[no-untyped-def]
            self.create_calls += 1
            raise Crash

    dying = Dying(contacts=case.mock.contacts)
    with pytest.raises(Crash):
        case.run(mock=dying)
    case.age_handoff(STALE_AFTER_S + 5)
    with pytest.raises(HandoffRefused) as err:
        case.run(mock=dying)
    assert err.value.problem is Problem.OUTCOME_UNKNOWN
    assert dying.create_calls == 1


def test_a_draft_that_comes_back_authorised_is_critical_and_left_alone(case: Case) -> None:
    case.mock.create_with_status("AUTHORISED")
    case.run()
    row = case.row()
    assert (row.status, row.failure_code, row.manual_action) == (
        "unknown", "UNEXPECTED_BILL_STATUS", True,
    )  # fmt: skip
    assert row.external_bill_id == "bill-1"
    assert "handoff_critical" in case.events()
    assert case.mock.create_calls == 1  # no corrective call of any kind


# ---- failures where nothing was sent --------------------------------------------------------


def test_a_rate_limited_request_is_retried_after_the_wait_the_provider_asked_for(
    case: Case,
) -> None:
    case.mock.fail_before_processing(Failure.RATE_LIMITED, retry_after_s=7)
    case.run()
    assert case.slept == [7]
    assert case.row().status == "succeeded" and len(case.mock.bills) == 1
    assert case.mock.create_calls == 2  # the first was refused unprocessed


def test_retries_are_bounded(case: Case) -> None:
    for _ in range(5):
        case.mock.fail_before_processing(Failure.RATE_LIMITED)
    case.run()
    assert case.row().status == "failed" and case.mock.create_calls == 3
    assert case.row().manual_action and len(case.mock.bills) == 0


@pytest.mark.parametrize("failure", [Failure.UNAUTHORISED, Failure.FORBIDDEN, Failure.REJECTED])
def test_authentication_permission_and_validation_failures_fail_without_retry(
    case: Case, failure: Failure
) -> None:
    case.mock.fail_before_processing(failure)
    case.run()
    row = case.row()
    assert (row.status, row.failure_code, row.manual_action) == ("failed", failure.value, True)
    assert case.mock.create_calls == 1 and case.slept == []


def test_a_failed_handoff_can_be_requested_again(case: Case) -> None:
    case.mock.fail_before_processing(Failure.UNAUTHORISED)
    case.run()
    case.run()
    assert case.row().status == "succeeded" and case.row().attempts == 2
    assert len(case.mock.bills) == 1


# ---- invoices and contacts that cannot be handed off safely ---------------------------------


def test_two_matching_contacts_stop_the_handoff_for_a_person(case: Case) -> None:
    case.mock.contacts.append(ContactCandidate("c-2", "ACME PTY LTD", ABN))
    case.run()
    assert case.row().status == "failed" and case.row().failure_code == "CONTACT_AMBIGUOUS"
    assert case.mock.create_calls == 0 and "contact_not_resolved" in case.events()


def test_no_contact_stops_the_handoff_and_none_is_created(case: Case) -> None:
    case.mock.contacts.clear()
    case.run()
    assert case.row().failure_code == "CONTACT_NOT_FOUND" and case.mock.create_calls == 0


def test_an_invoice_with_a_missing_date_is_refused_before_any_write(
    case: Case, engine: Engine
) -> None:
    with Session(engine) as s:
        inv = s.get(Invoice, case.invoice)
        assert inv is not None
        inv.invoice_date = None
        s.commit()
    case.run()
    assert case.row().failure_code == "INVOICE_DATE_MISSING"
    assert case.mock.create_calls == 0 and "mapping_failed" in case.events()


def test_a_bill_already_in_the_books_is_not_duplicated(case: Case) -> None:
    case.mock.add_existing_bill("c-1", "INV-001")
    case.run()
    assert case.row().failure_code == "BILL_ALREADY_EXISTS" and case.mock.create_calls == 0


def test_a_read_failure_before_the_write_fails_cleanly(case: Case) -> None:
    from intake.connectors.accounting import ProviderError

    class Down(MockAccounting):
        def find_contacts(self, name, tax_id):  # type: ignore[no-untyped-def]
            raise ProviderError(Failure.TIMEOUT)

    m = Down()
    case.run(mock=m)
    assert case.row().status == "failed" and case.row().failure_code == "timeout"
    assert m.create_calls == 0


def test_an_unexpected_error_before_the_write_releases_the_claim(case: Case) -> None:
    class Broken(MockAccounting):
        def find_contacts(self, name, tax_id):  # type: ignore[no-untyped-def]
            raise ValueError("boom")

    case.run(mock=Broken())
    assert case.row().status == "failed"
    assert (case.row().failure_code or "").startswith("INTERNAL_ERROR:ValueError")


def test_an_unreadable_answer_after_sending_is_an_unknown_outcome(case: Case) -> None:
    class Garbled(MockAccounting):
        def create_draft_bill(self, bill, key):  # type: ignore[no-untyped-def]
            super().create_draft_bill(bill, key)
            raise KeyError("unexpected shape")

    g = Garbled(contacts=case.mock.contacts)
    case.run(mock=g)
    assert case.row().status == "unknown" and "draft_bill_error" in case.events()
    assert g.create_calls == 1


def test_reconciling_when_several_bills_match_is_left_to_a_person(
    case: Case, engine: Engine
) -> None:
    case.mock.lose_response()
    case.run()
    case.mock.bills.append(case.mock.bills[0])  # two bills of ours with the same number
    with Session(engine) as s:
        out = reconcile(
            s, connector=case.mock, tax_types=TAX, tenant_id=case.tenant,
            invoice_id=case.invoice, actor="system",
        )  # fmt: skip
    assert out.status is HandoffStatus.UNKNOWN


def test_handoffs_to_different_organisations_are_independent(case: Case) -> None:
    case.run()
    other = MockAccounting(contacts=case.mock.contacts, organisation_id="other-org")
    case.run(mock=other)
    assert other.create_calls == 1
    with Session(case.engine) as s:
        keys = list(s.scalars(select(AccountingHandoff.handoff_key)))
    assert len(set(keys)) == len(keys)
