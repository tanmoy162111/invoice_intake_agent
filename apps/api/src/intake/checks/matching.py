"""Stage 6 of the pipeline: 3-way match with the purchase order and goods receipts (playbook §6.6).

Runs after the duplicate check, on an invoice in `checking`, and records one result per match code.
The comparison itself is pure (`core/match.py`); this stage only gathers what it needs: the PO
candidates, the receipts, and what earlier invoices already billed against each PO.

"Earlier" means received earlier (ties broken by id), the same order as the duplicate check. Before
matching, the stage waits (a deferral, not a failure) for earlier invoices that are not read or not
matched yet, because what they bill decides whether this invoice over-bills a PO. If the wait runs
out, a check that depends on earlier billing and would have passed is reported as `skipped`, never
as a pass. The lines that matched a PO line are recorded on the invoice lines, which is how later
invoices see what was billed. The invoice stays in `checking` (routing is M7). Nothing here pays or
moves money.
"""

import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import BigInteger, ColumnElement, cast, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from intake.audit.writer import record_event
from intake.checks.duplicates import _earlier
from intake.checks.pipeline import WrongTenant
from intake.config import Settings
from intake.core.match import (
    RULE_VERSION,
    InvoiceLineFacts,
    MatchCheck,
    MatchCode,
    MatchInvoice,
    MatchResult,
    MatchSettings,
    PoLineRef,
    PoRef,
    check_match,
    normalize_po_number,
)
from intake.core.statuses import ActorType, InvoiceStatus
from intake.core.validate import Outcome
from intake.db.models import (
    CheckResult,
    GoodsReceipt,
    Invoice,
    InvoiceLine,
    PoLine,
    PurchaseOrder,
    ReceiptLine,
    Tenant,
)
from intake.extract.service import db_now
from intake.worker import queue

MATCH_JOB = "match_invoice"
MAX_POS = 2000  # beyond this the comparison is refused, never silently truncated
WAITING = "WAITING_FOR_EARLIER_INVOICES"
# Not read or not checked yet, so they may still bill against a PO.
_PENDING = (
    InvoiceStatus.RECEIVED,
    InvoiceStatus.EXTRACTING,
    InvoiceStatus.EXTRACTED,
    InvoiceStatus.CHECKING,
)
_NEVER_BILLED = (InvoiceStatus.FAILED, InvoiceStatus.REJECTED)
# Checks whose answer depends on what earlier invoices billed.
_CUMULATIVE = (MatchCode.QTY_VARIANCE, MatchCode.QTY_NOT_RECEIVED, MatchCode.PO_OVERBILLED)
_MARKER = MatchCode.NO_PO  # written with every other match result, in the same transaction


def _po_key(column: InstrumentedAttribute[str]) -> ColumnElement[str]:
    return func.regexp_replace(func.upper(column), "[^A-Z0-9]", "", "g")


def _candidate_pos(session: Session, inv: Invoice) -> list[PurchaseOrder] | None:
    """The PO the invoice names (any supplier) and the supplier's own POs; None if too many."""
    key = normalize_po_number(inv.po_number)
    wanted = []
    if key:
        wanted.append(_po_key(PurchaseOrder.po_number) == key)
    if inv.supplier_id:
        wanted.append(PurchaseOrder.supplier_id == inv.supplier_id)
    if not wanted:
        return []
    rows = list(
        session.execute(
            select(PurchaseOrder)
            .where(PurchaseOrder.tenant_id == inv.tenant_id, or_(*wanted))
            .order_by(PurchaseOrder.po_number)
            .limit(MAX_POS + 1)
        ).scalars()
    )
    return None if len(rows) > MAX_POS else rows


def _po_refs(
    session: Session, inv: Invoice, pos: list[PurchaseOrder]
) -> tuple[list[PoRef], dict[str, int]]:
    """PO references with receipts and earlier billing, and per PO how many earlier bills are
    unknown (an earlier invoice matched to it whose quantity or amount could not be read)."""
    if not pos:
        return [], {}
    tenant_id = inv.tenant_id
    po_ids = [p.id for p in pos]
    lines = session.execute(
        select(PoLine)
        .where(PoLine.tenant_id == tenant_id, PoLine.po_id.in_(po_ids))
        .order_by(PoLine.po_id, PoLine.line_no)
    ).scalars().all()  # fmt: skip
    # a subquery, not a list of ids: the number of bound parameters must not grow with the lines
    line_ids = select(PoLine.id).where(PoLine.tenant_id == tenant_id, PoLine.po_id.in_(po_ids))

    received: dict[uuid.UUID, Decimal] = dict(
        session.execute(
            select(ReceiptLine.po_line_id, func.sum(ReceiptLine.qty_received))
            .where(ReceiptLine.tenant_id == tenant_id, ReceiptLine.po_line_id.in_(line_ids))
            .group_by(ReceiptLine.po_line_id)
        )
        .tuples()
        .all()  # fmt: skip
    )
    receipts: dict[uuid.UUID, int] = dict(
        session.execute(
            select(GoodsReceipt.po_id, func.count())
            .where(GoodsReceipt.tenant_id == tenant_id, GoodsReceipt.po_id.in_(po_ids))
            .group_by(GoodsReceipt.po_id)
        )
        .tuples()
        .all()  # fmt: skip
    )

    earlier = _earlier(inv)
    counted = Invoice.status.not_in([s.value for s in _NEVER_BILLED])
    po_of_line = {ln.id: str(ln.po_id) for ln in lines}
    billed_qty: dict[uuid.UUID, Decimal] = {}
    unknown: dict[str, int] = defaultdict(int)
    for line_id, qty, n_unreadable in session.execute(
        select(InvoiceLine.matched_po_line_id, func.sum(InvoiceLine.qty),
               func.count().filter(InvoiceLine.qty.is_(None)))
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(
            InvoiceLine.tenant_id == tenant_id, earlier, counted,
            InvoiceLine.matched_po_line_id.in_(line_ids),
        )
        .group_by(InvoiceLine.matched_po_line_id)
    ).tuples():  # fmt: skip
        if line_id is not None:
            billed_qty[line_id] = qty or Decimal(0)
            unknown[po_of_line[line_id]] += n_unreadable

    # Summed in the database, current rule version only (an older version's row for the same
    # invoice must not be counted again). A missing or null amount counts as unknown.
    result_po = CheckResult.details["po_id"].astext
    amount = CheckResult.details["billed_minor"].astext
    billed_minor: dict[str, int] = defaultdict(int)
    for po_key, total, n_unknown in session.execute(
        select(result_po, func.coalesce(func.sum(cast(amount, BigInteger)), 0),
               func.count().filter(amount.is_(None)))
        .join(Invoice, Invoice.id == CheckResult.invoice_id)
        .where(
            CheckResult.tenant_id == tenant_id, earlier, counted,
            CheckResult.check_code == MatchCode.PO_OVERBILLED.value,
            CheckResult.rule_version == RULE_VERSION,
            result_po.in_([str(i) for i in po_ids]),
        )
        .group_by(result_po)
    ).tuples():  # fmt: skip
        billed_minor[po_key] += int(total)
        unknown[po_key] += n_unknown

    by_po: dict[uuid.UUID, list[PoLineRef]] = defaultdict(list)
    for ln in lines:
        by_po[ln.po_id].append(
            PoLineRef(
                id=str(ln.id), line_no=ln.line_no, sku=ln.sku, description=ln.description,
                qty=ln.qty, unit_price_minor=ln.unit_price_minor,
                qty_received=received.get(ln.id, Decimal(0)),
                qty_billed_before=billed_qty.get(ln.id, Decimal(0)),
            )
        )  # fmt: skip
    refs = [
        PoRef(
            id=str(p.id), po_number=p.po_number, supplier_id=str(p.supplier_id),
            currency=p.currency, total_minor=p.total_minor, status=p.status,
            lines=tuple(by_po[p.id]), receipt_count=receipts.get(p.id, 0),
            billed_before_minor=billed_minor[str(p.id)],
        )
        for p in pos
    ]  # fmt: skip
    return refs, dict(unknown)


def _facts(inv: Invoice, lines: list[InvoiceLine]) -> MatchInvoice:
    return MatchInvoice(
        id=str(inv.id),
        supplier_id=str(inv.supplier_id) if inv.supplier_id else None,
        po_number=inv.po_number,
        currency=inv.currency,
        subtotal_minor=inv.subtotal_minor,
        lines=tuple(
            InvoiceLineFacts(
                ln.line_no, ln.sku, ln.description, ln.qty, ln.unit_price_minor, ln.amount_minor
            )
            for ln in lines
        ),
    )


def _uncertain(check: MatchCheck, reason: str) -> MatchCheck:
    if check.code in _CUMULATIVE and check.outcome is Outcome.PASS:
        return MatchCheck(check.code, Outcome.SKIPPED, {"reason": reason})
    return check


def _too_many() -> MatchResult:
    checks = tuple(
        MatchCheck(c, Outcome.SKIPPED, {"reason": "TOO_MANY_POS_TO_COMPARE"}) for c in MatchCode
    )
    return MatchResult(checks, None, False, ())


def match_invoice(
    session: Session,
    settings: Settings,
    invoice_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    stop_waiting: bool = False,
) -> queue.Deferral | None:
    inv = session.get_one(Invoice, invoice_id)
    if inv.tenant_id != tenant_id:
        raise WrongTenant
    if InvoiceStatus(inv.status) is not InvoiceStatus.CHECKING:
        return None  # the checks have not run, or the invoice is further along
    done = session.execute(
        select(CheckResult.id).where(
            CheckResult.tenant_id == tenant_id,
            CheckResult.invoice_id == inv.id,
            CheckResult.check_code == _MARKER.value,
            CheckResult.rule_version == RULE_VERSION,
        )
    ).first()
    if done is not None:
        return None  # already matched: nothing to do

    earlier = _earlier(inv)
    matched = select(CheckResult.invoice_id).where(
        CheckResult.tenant_id == tenant_id,
        CheckResult.check_code == _MARKER.value,
        CheckResult.rule_version == RULE_VERSION,
    )
    pending = session.scalar(
        select(func.count()).select_from(Invoice).where(
            earlier,
            Invoice.status.in_([s.value for s in _PENDING]),
            Invoice.id.not_in(matched),
        )
    ) or 0  # fmt: skip
    if pending and not stop_waiting:
        until = db_now(session) + timedelta(seconds=settings.match_poll_s)
        return queue.Deferral(until, WAITING)

    lines = list(
        session.execute(
            select(InvoiceLine)
            .where(InvoiceLine.invoice_id == inv.id)
            .order_by(InvoiceLine.line_no)
        ).scalars()
    )
    tenant = session.get_one(Tenant, inv.tenant_id)
    candidates = _candidate_pos(session, inv)
    unknown: dict[str, int] = {}
    if candidates is None:
        result = _too_many()
    else:
        refs, unknown = _po_refs(session, inv, candidates)
        result = check_match(
            _facts(inv, lines), refs, MatchSettings.from_tenant(tenant.settings or {})
        )
    checks = list(result.checks)
    reason = "EARLIER_INVOICES_STILL_PENDING" if pending else "EARLIER_BILLING_UNKNOWN"
    if pending or (result.po_id and unknown.get(result.po_id)):
        checks = [_uncertain(c, reason) for c in checks]

    po_id = uuid.UUID(result.po_id) if result.po_id else None
    for c in checks:
        details: dict[str, object] = {**c.details, "outcome": c.outcome.value}
        if c.code is MatchCode.PO_OVERBILLED:  # what later invoices need to see
            details["po_id"] = result.po_id
            details["billed_minor"] = result.billed_minor
        if c.code is _MARKER:
            details["inferred"] = result.inferred
        if pending:
            details["pending_earlier"] = pending
        session.add(
            CheckResult(
                tenant_id=inv.tenant_id, invoice_id=inv.id, check_code=c.code.value,
                passed=c.outcome is Outcome.PASS, details=details, rule_version=RULE_VERSION,
            )
        )  # fmt: skip
    by_no = {ln.line_no: ln for ln in lines}
    for m in result.line_matches:
        by_no[m.invoice_line_no].matched_po_line_id = uuid.UUID(m.po_line_id)
    record_event(
        session, tenant_id=inv.tenant_id, invoice_id=inv.id, event_type="match_completed",
        actor_type=ActorType.SYSTEM, actor_id="worker",
        data={
            "rule_version": RULE_VERSION, "po_id": str(po_id) if po_id else None,
            "inferred": result.inferred,
            "outcomes": {c.code.value: c.outcome.value for c in checks},
            "line_matches": [
                [m.invoice_line_no, m.method] for m in result.line_matches
            ],
            "pending_earlier": pending,
        },
    )  # fmt: skip
    return None
