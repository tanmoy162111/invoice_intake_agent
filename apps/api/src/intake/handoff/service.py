"""Handing an approved invoice to an accounting system as a draft bill (Project B).

Creates draft bills only. A human must review and approve bills in the accounting platform.

The accounting system and our database cannot commit together, so the order is what keeps this
safe: (1) under a lock on the invoice, check it is still approved and that no other handoff exists,
and durably claim it (`in_progress`); (2) look up the contact and build the bill, writing nothing;
(3) record that a write is about to be attempted; (4) write once; (5) record what the provider said.
A crash or timeout after (3) leaves `in_progress` or `unknown`, never a second write: the only ways
out are reconciliation against the provider or a person's decision. Rules live in
`core/accounting.py`; every state change here writes an audit event (codes and ids, no values).
"""

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.connectors.accounting import AccountingConnector, ProviderError
from intake.core.accounting import (
    HANDOFF_TRANSITIONS,
    BillDraft,
    ContactCandidate,
    Failure,
    InvoiceFacts,
    LineFacts,
    Next,
    Problem,
    decide_after_failure,
    handoff_key,
    handoff_problem,
    is_stale,
    map_invoice,
    match_contact,
    verify_created,
)
from intake.core.statuses import ActorType, HandoffStatus, InvoiceStatus
from intake.db.models import (
    AccountingHandoff,
    Invoice,
    InvoiceLine,
    Supplier,
    SupplierAccountingLink,
)

Sleep = Callable[[float], None]


class NotFound(Exception):
    """No such invoice for this tenant (another tenant's is the same as none)."""


class HandoffRefused(Exception):
    def __init__(self, problem: Problem) -> None:
        super().__init__(problem.value)
        self.problem = problem


@dataclass(frozen=True)
class HandoffView:
    status: HandoffStatus
    provider: str
    organisation_id: str
    external_bill_id: str | None
    failure_code: str | None
    manual_action: bool
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None


def view(h: AccountingHandoff) -> HandoffView:
    return HandoffView(
        HandoffStatus(h.status), h.provider, h.organisation_id, h.external_bill_id,
        h.failure_code, h.manual_action, h.attempts, h.started_at, h.finished_at,
    )  # fmt: skip


def _event(session: Session, h: AccountingHandoff, event: str, actor: str, **data: object) -> None:
    record_event(
        session, tenant_id=h.tenant_id, invoice_id=h.invoice_id, event_type=event,
        actor_type=ActorType.USER if actor != "system" else ActorType.SYSTEM, actor_id=actor,
        data={"handoff_id": str(h.id), "provider": h.provider, "organisation": h.organisation_id,
              **data},
    )  # fmt: skip


def _move(h: AccountingHandoff, new: HandoffStatus) -> None:
    current = HandoffStatus(h.status)
    if new not in HANDOFF_TRANSITIONS[current]:
        raise RuntimeError(f"handoff {current.value} -> {new.value} is not allowed")
    h.status = new.value


def _lock_invoice(session: Session, tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    inv = session.get(Invoice, invoice_id, with_for_update=True, populate_existing=True)
    if inv is None or inv.tenant_id != tenant_id:
        raise NotFound
    return inv


def get_handoff(
    session: Session, tenant_id: uuid.UUID, invoice_id: uuid.UUID, provider: str, organisation: str
) -> AccountingHandoff | None:
    return session.scalars(
        select(AccountingHandoff).where(
            AccountingHandoff.tenant_id == tenant_id,
            AccountingHandoff.invoice_id == invoice_id,
            AccountingHandoff.provider == provider,
            AccountingHandoff.organisation_id == organisation,
        )
    ).first()


def _stale_to_unknown(session: Session, h: AccountingHandoff, now: datetime) -> bool:
    if (
        h.status == HandoffStatus.IN_PROGRESS.value
        and h.started_at is not None
        and is_stale(h.started_at, now)
    ):
        _move(h, HandoffStatus.UNKNOWN)
        h.failure_code, h.manual_action, h.finished_at = Problem.WORKER_INTERRUPTED.value, True, now
        _event(session, h, "handoff_outcome_unknown", "system", code=h.failure_code)
        return True
    return False


def recover_stale(session: Session, *, tenant_id: uuid.UUID, now: datetime | None = None) -> int:
    """A handoff left `in_progress` by a worker that died may or may not have written. It becomes
    `unknown` (to be reconciled), never `failed`. Returns how many were changed."""
    now = now or datetime.now(UTC)
    rows = session.scalars(
        select(AccountingHandoff)
        .where(
            AccountingHandoff.tenant_id == tenant_id,
            AccountingHandoff.status == HandoffStatus.IN_PROGRESS.value,
        )
        .with_for_update()
    ).all()
    changed = sum(_stale_to_unknown(session, h, now) for h in rows)
    session.commit()
    return changed


def _facts(session: Session, inv: Invoice) -> InvoiceFacts:
    lines = session.scalars(
        select(InvoiceLine)
        .where(InvoiceLine.tenant_id == inv.tenant_id, InvoiceLine.invoice_id == inv.id)
        .order_by(InvoiceLine.line_no)
    ).all()
    return InvoiceFacts(
        inv.invoice_number, inv.invoice_date, inv.due_date, inv.currency, inv.subtotal_minor,
        inv.tax_minor, inv.total_minor,
        tuple(
            LineFacts(ln.description, ln.qty, ln.unit_price_minor, ln.amount_minor, ln.tax_rate)
            for ln in lines
        ),
    )  # fmt: skip


def _finish(
    session: Session, h: AccountingHandoff, new: HandoffStatus, event: str, *, actor: str,
    code: str | None = None, manual: bool = False, bill_id: str | None = None,
) -> HandoffView:  # fmt: skip
    _move(h, new)
    h.failure_code, h.manual_action = code, manual
    if bill_id:
        h.external_bill_id = bill_id
    h.finished_at = datetime.now(UTC)
    _event(session, h, event, actor, code=code, manual_action=manual, bill_id=bill_id)
    session.commit()
    return view(h)


def _fail_unwritten(
    session: Session, h: AccountingHandoff, actor: str, code: str, manual: bool = True
) -> HandoffView:
    """Failed before anything was sent: safe to try again once the cause is fixed."""
    return _finish(session, h, HandoffStatus.FAILED, "handoff_failed", actor=actor, code=code,
                   manual=manual)  # fmt: skip


def request_handoff(
    session: Session,
    *,
    connector: AccountingConnector,
    tax_types: Mapping[int, str],
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    actor: str,
    sleep: Sleep = time.sleep,
    now: datetime | None = None,
) -> HandoffView:
    now = now or datetime.now(UTC)
    prov, org = connector.provider, connector.organisation_id

    # (1) check and claim, under a lock on the invoice so two requests cannot both pass
    inv = _lock_invoice(session, tenant_id, invoice_id)
    h = get_handoff(session, tenant_id, invoice_id, prov, org)
    if h is not None:
        _stale_to_unknown(session, h, now)
    problem = handoff_problem(
        InvoiceStatus(inv.status), HandoffStatus(h.status) if h is not None else None
    )
    if problem is not None:
        record_event(
            session, tenant_id=tenant_id, invoice_id=invoice_id, event_type="handoff_refused",
            actor_type=ActorType.USER, actor_id=actor,
            data={"code": problem.value, "provider": prov, "organisation": org},
        )  # fmt: skip
        session.commit()
        raise HandoffRefused(problem)
    if h is None:
        h = AccountingHandoff(
            tenant_id=tenant_id, invoice_id=invoice_id, provider=prov, organisation_id=org,
            handoff_key=handoff_key(tenant_id, prov, org, invoice_id),
            status=HandoffStatus.PENDING.value, attempts=0, manual_action=False,
        )  # fmt: skip
        session.add(h)
        session.flush()
    _move(h, HandoffStatus.IN_PROGRESS)
    h.attempts += 1
    h.started_at, h.finished_at, h.failure_code, h.manual_action = now, None, None, False
    h.requested_by = actor
    _event(session, h, "handoff_requested", actor, attempt=h.attempts, invoice_status=inv.status)
    session.commit()

    # (2) find the contact and build the bill: reads only, nothing is written anywhere
    try:
        facts = _facts(session, inv)
        supplier = session.get(Supplier, inv.supplier_id) if inv.supplier_id else None
        name = supplier.name if supplier is not None else (inv.supplier_name or "")
        tax_id = supplier.tax_id if supplier is not None else None
        link = (
            session.scalars(
                select(SupplierAccountingLink.contact_id).where(
                    SupplierAccountingLink.tenant_id == tenant_id,
                    SupplierAccountingLink.supplier_id == supplier.id,
                    SupplierAccountingLink.provider == prov,
                    SupplierAccountingLink.organisation_id == org,
                )
            ).first()
            if supplier is not None
            else None
        )
        candidates: list[ContactCandidate]
        if link is not None:
            found = connector.get_contact(link)
            candidates = [found] if found is not None else []
        else:
            candidates = connector.find_contacts(name, tax_id)
        decision = match_contact(name, tax_id, link, candidates)
        if decision.contact_id is None or decision.problem is not None:
            problem = decision.problem or Problem.CONTACT_NOT_FOUND
            _event(session, h, "contact_not_resolved", actor, code=problem.value)
            return _fail_unwritten(session, h, actor, problem.value)
        h.contact_id = decision.contact_id
        mapped = map_invoice(facts, decision.contact_id, tax_types)
        if mapped.bill is None:
            _event(session, h, "mapping_failed", actor, problems=[p.value for p in mapped.problems])
            return _fail_unwritten(session, h, actor, mapped.problems[0].value)
        bill = mapped.bill
        _event(session, h, "mapping_passed", actor, contact_matched_by=decision.matched_by)
        if connector.find_bills(bill.contact_id, bill.invoice_number, created_by_us=False):
            return _fail_unwritten(session, h, actor, Problem.BILL_ALREADY_EXISTS.value)
    except ProviderError as exc:  # reads only: whatever happened, nothing was written
        return _fail_unwritten(session, h, actor, exc.failure.value)
    except Exception as exc:  # a bug must not leave a claim hanging
        session.rollback()
        h = _reload(session, h)
        return _fail_unwritten(session, h, actor, f"INTERNAL_ERROR:{type(exc).__name__}")

    # (3) re-check approval right before the write, and record the attempt before making it
    inv = _lock_invoice(session, tenant_id, invoice_id)
    if inv.status != InvoiceStatus.APPROVED.value:
        return _fail_unwritten(session, h, actor, Problem.NOT_APPROVED.value, manual=False)
    _event(session, h, "draft_bill_attempted", actor, key=h.handoff_key)
    session.commit()

    # (4) write once; only a request the provider cannot have acted on is ever repeated
    attempt = 0
    while True:
        attempt += 1
        try:
            got = connector.create_draft_bill(bill, h.handoff_key)
            break
        except ProviderError as exc:
            decision_ = decide_after_failure(exc.failure, attempt, exc.retry_after_s)
            if decision_.next is Next.RETRY:
                _event(session, h, "draft_bill_retry", actor, code=exc.failure.value,
                       delay_s=decision_.delay_s)  # fmt: skip
                session.commit()
                sleep(decision_.delay_s)
                continue
            return _after_failure(
                session, h, actor, exc.failure, decision_.next, decision_.manual_action
            )
        except Exception as exc:  # unreadable or unexpected: the write may have happened
            _event(session, h, "draft_bill_error", actor, error=type(exc).__name__)
            return _after_failure(session, h, actor, Failure.BAD_RESPONSE, Next.UNKNOWN, True)

    # (5) success means the bill exists, is a draft and says what we sent
    problem = verify_created(bill, got)
    if problem is None:
        return _finish(session, h, HandoffStatus.SUCCEEDED, "draft_bill_created", actor=actor,
                       bill_id=got.bill_id)  # fmt: skip
    # a bill exists but is not what we asked for: recorded, left alone, no corrective call
    return _finish(
        session, h, HandoffStatus.UNKNOWN, "handoff_critical", actor=actor, code=problem.value,
        manual=True, bill_id=got.bill_id or None,
    )  # fmt: skip


def _reload(session: Session, h: AccountingHandoff) -> AccountingHandoff:
    fresh = session.get(AccountingHandoff, h.id, populate_existing=True)
    assert fresh is not None
    return fresh


def _after_failure(
    session: Session, h: AccountingHandoff, actor: str, failure: Failure, nxt: Next, manual: bool
) -> HandoffView:
    if nxt is Next.FAIL:
        return _finish(session, h, HandoffStatus.FAILED, "handoff_failed", actor=actor,
                       code=failure.value, manual=manual)  # fmt: skip
    return _finish(session, h, HandoffStatus.UNKNOWN, "handoff_outcome_unknown", actor=actor,
                   code=failure.value, manual=True)  # fmt: skip


# ---- finding out what happened after an unknown outcome ------------------------------------------


def _unknown_handoff(
    session: Session, tenant_id: uuid.UUID, invoice_id: uuid.UUID, provider: str, org: str
) -> AccountingHandoff:
    _lock_invoice(session, tenant_id, invoice_id)
    h = get_handoff(session, tenant_id, invoice_id, provider, org)
    if h is None or h.status != HandoffStatus.UNKNOWN.value:
        raise NotFound
    return h


def reconcile(
    session: Session,
    *,
    connector: AccountingConnector,
    tax_types: Mapping[int, str],
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    actor: str,
) -> HandoffView:
    """Look for the bill this application may have created. Exactly one matching draft proves the
    write happened. Anything else (none, several, a different bill) proves nothing, so the handoff
    stays `unknown` for a person; absence is never taken as permission to write again."""
    h = _unknown_handoff(
        session, tenant_id, invoice_id, connector.provider, connector.organisation_id
    )
    inv = _lock_invoice(session, tenant_id, invoice_id)
    mapped = map_invoice(_facts(session, inv), h.contact_id or "", tax_types)
    bill: BillDraft | None = mapped.bill if h.contact_id else None
    found = (
        connector.find_bills(bill.contact_id, bill.invoice_number, created_by_us=True)
        if bill is not None
        else []
    )
    if bill is not None and len(found) == 1 and verify_created(bill, found[0]) is None:
        return _finish(session, h, HandoffStatus.SUCCEEDED, "handoff_reconciled", actor=actor,
                       bill_id=found[0].bill_id)  # fmt: skip
    _event(session, h, "reconciliation_inconclusive", actor, matches=len(found))
    h.manual_action = True
    session.commit()
    return view(h)


def resolve_unknown(
    session: Session,
    *,
    provider: str,
    organisation_id: str,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    actor: str,
    bill_id: str | None,
) -> HandoffView:
    """A person checked the accounting system. With a bill id: it exists (`succeeded`). Without:
    they confirm none exists (`failed`, so the handoff may be requested again)."""
    h = _unknown_handoff(session, tenant_id, invoice_id, provider, organisation_id)
    if bill_id:
        return _finish(session, h, HandoffStatus.SUCCEEDED, "handoff_resolved_found", actor=actor,
                       bill_id=bill_id.strip())  # fmt: skip
    return _finish(session, h, HandoffStatus.FAILED, "handoff_resolved_absent", actor=actor,
                   code=Problem.OUTCOME_UNKNOWN.value)  # fmt: skip
