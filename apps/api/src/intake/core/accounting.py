"""Accounting hand-off rules (Project B). Pure: facts in, a decision out. No provider, no database.

The agent may prepare a **draft** supplier bill; a person approves and pays it in the accounting
system. Nothing here approves, pays or moves money, and nothing guesses: a missing date, an unmapped
tax rate, an unclear contact or an amount that does not add up is a problem for a person, found
before anything is written. Amounts are integer minor units and never change on the way through.
"""

import hashlib
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from intake.core.money import CURRENCY_EXPONENT, line_amount_minor
from intake.core.statuses import HandoffStatus, InvoiceStatus

DRAFT = "DRAFT"  # the only status a bill is ever created in
STALE_AFTER_S = 15 * 60  # a handoff "in progress" for this long belonged to a worker that died
MAX_ATTEMPTS = 3
RETRY_AFTER_MAX_S = 120  # a longer wait than this is left to a person, not slept through


class Problem(StrEnum):
    # who may be handed off
    NOT_APPROVED = "NOT_APPROVED"
    ALREADY_HANDED_OFF = "ALREADY_HANDED_OFF"
    HANDOFF_IN_PROGRESS = "HANDOFF_IN_PROGRESS"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    # building the bill
    INVOICE_NUMBER_MISSING = "INVOICE_NUMBER_MISSING"
    INVOICE_DATE_MISSING = "INVOICE_DATE_MISSING"
    CURRENCY_UNSUPPORTED = "CURRENCY_UNSUPPORTED"
    NO_LINES = "NO_LINES"
    LINE_DESCRIPTION_MISSING = "LINE_DESCRIPTION_MISSING"
    LINE_AMOUNT_MISSING = "LINE_AMOUNT_MISSING"
    TOTALS_MISSING = "TOTALS_MISSING"
    TOTALS_MISMATCH = "TOTALS_MISMATCH"
    TAX_MAPPING_MISSING = "TAX_MAPPING_MISSING"
    TAX_MISMATCH = "TAX_MISMATCH"
    BILL_ALREADY_EXISTS = "BILL_ALREADY_EXISTS"  # same supplier and number already in the books
    WORKER_INTERRUPTED = "WORKER_INTERRUPTED"  # a handoff began and never reported back
    NOT_A_BILL = "NOT_A_BILL"  # a credit note or an empty invoice: not supported in v1
    # the supplier's contact
    CONTACT_NOT_FOUND = "CONTACT_NOT_FOUND"
    CONTACT_AMBIGUOUS = "CONTACT_AMBIGUOUS"
    CONTACT_CONFLICT = "CONTACT_CONFLICT"
    # what the provider said it created
    UNEXPECTED_BILL_STATUS = "UNEXPECTED_BILL_STATUS"
    RESPONSE_MISMATCH = "RESPONSE_MISMATCH"


# ---- hand-off state ----------------------------------------------------------------------------

# `unknown` leaves only through reconciliation, which finds the bill (`succeeded`) or proves there
# is none (`failed`). It never goes straight back to `in_progress`.
HANDOFF_TRANSITIONS: dict[HandoffStatus, frozenset[HandoffStatus]] = {
    HandoffStatus.PENDING: frozenset({HandoffStatus.IN_PROGRESS}),
    HandoffStatus.IN_PROGRESS: frozenset(
        {HandoffStatus.SUCCEEDED, HandoffStatus.FAILED, HandoffStatus.UNKNOWN}
    ),
    HandoffStatus.FAILED: frozenset({HandoffStatus.IN_PROGRESS}),
    HandoffStatus.UNKNOWN: frozenset({HandoffStatus.SUCCEEDED, HandoffStatus.FAILED}),
    HandoffStatus.SUCCEEDED: frozenset(),
}


def handoff_problem(invoice: InvoiceStatus, existing: HandoffStatus | None) -> Problem | None:
    """Only a human-approved invoice (the existing review decision; model confidence is not one)
    and only when no other handoff is running, finished or unresolved."""
    if invoice is not InvoiceStatus.APPROVED:
        return Problem.NOT_APPROVED
    if existing is None or existing is HandoffStatus.FAILED:
        return None
    if existing is HandoffStatus.SUCCEEDED:
        return Problem.ALREADY_HANDED_OFF
    if existing is HandoffStatus.UNKNOWN:
        return Problem.OUTCOME_UNKNOWN
    return Problem.HANDOFF_IN_PROGRESS


def is_stale(started_at: datetime, now: datetime) -> bool:
    return now - started_at >= timedelta(seconds=STALE_AFTER_S)


def handoff_key(
    tenant_id: uuid.UUID, provider: str, organisation: str, invoice_id: uuid.UUID
) -> str:
    """The same invoice, provider and organisation always give the same key (sent to the provider as
    its idempotency key, within its 128 character limit and never relied on alone)."""
    raw = f"{tenant_id}|{provider}|{organisation}|{invoice_id}"
    return "ii1-" + hashlib.sha256(raw.encode()).hexdigest()


# ---- invoice -> draft bill ---------------------------------------------------------------------


@dataclass(frozen=True)
class LineFacts:
    description: str | None
    qty: Decimal | None
    unit_price_minor: int | None
    amount_minor: int | None
    tax_rate: Decimal | None  # percent (10 means 10%), as stored on the invoice line


@dataclass(frozen=True)
class InvoiceFacts:
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str | None
    subtotal_minor: int | None
    tax_minor: int | None
    total_minor: int | None
    lines: tuple[LineFacts, ...]


@dataclass(frozen=True)
class BillLine:
    description: str
    amount_minor: int
    tax_type: str
    quantity: Decimal | None = None
    unit_amount_minor: int | None = None
    account_code: str | None = None  # left blank: no approved account mapping exists in v1


@dataclass(frozen=True)
class BillDraft:
    contact_id: str
    invoice_number: str
    invoice_date: date
    due_date: date | None
    currency: str
    subtotal_minor: int
    tax_minor: int
    total_minor: int
    lines: tuple[BillLine, ...]


@dataclass(frozen=True)
class MapResult:
    bill: BillDraft | None
    problems: tuple[Problem, ...]


def _line_tax(amount_minor: int, rate_bp: int) -> int:
    sign = -1 if amount_minor < 0 else 1
    tax = (Decimal(abs(amount_minor)) * rate_bp / 10000).quantize(Decimal(1), ROUND_HALF_UP)
    return sign * int(tax)


def _rate_bp(rate: Decimal | None) -> int | None:
    """Percent to basis points, only when exact (10.555% is not a rate anyone mapped)."""
    if rate is None:
        return None
    bp = rate * 100
    return int(bp) if bp == bp.to_integral_value() else None


def map_invoice(facts: InvoiceFacts, contact_id: str, tax_types: Mapping[int, str]) -> MapResult:
    """Build the draft bill, or say everything that stops one. `tax_types` maps a tax rate in basis
    points to the provider's tax type; a rate without an entry is never guessed."""
    problems: list[Problem] = []
    number = (facts.invoice_number or "").strip()
    if not number:
        problems.append(Problem.INVOICE_NUMBER_MISSING)
    if facts.invoice_date is None:
        problems.append(Problem.INVOICE_DATE_MISSING)
    if facts.currency is None or facts.currency not in CURRENCY_EXPONENT:
        problems.append(Problem.CURRENCY_UNSUPPORTED)
    if not facts.lines:
        problems.append(Problem.NO_LINES)

    lines: list[BillLine] = []
    tax_sum = 0
    for ln in facts.lines:
        desc = (ln.description or "").strip()
        if not desc:
            problems.append(Problem.LINE_DESCRIPTION_MISSING)
        if ln.amount_minor is None:
            problems.append(Problem.LINE_AMOUNT_MISSING)
        bp = _rate_bp(ln.tax_rate)
        tax_type = tax_types.get(bp) if bp is not None else None
        if tax_type is None:
            problems.append(Problem.TAX_MAPPING_MISSING)
        if not desc or ln.amount_minor is None or bp is None or tax_type is None:
            continue
        tax_sum += _line_tax(ln.amount_minor, bp)
        # Quantity and unit price go across only when they give exactly the line amount; otherwise
        # the amount alone does, so the provider cannot recompute a different one.
        consistent = (
            ln.qty is not None
            and ln.unit_price_minor is not None
            and line_amount_minor(ln.qty, ln.unit_price_minor) == ln.amount_minor
        )
        lines.append(
            BillLine(
                description=desc, amount_minor=ln.amount_minor, tax_type=tax_type,
                quantity=ln.qty if consistent else None,
                unit_amount_minor=ln.unit_price_minor if consistent else None,
            )
        )  # fmt: skip

    sub, tax, total = facts.subtotal_minor, facts.tax_minor, facts.total_minor
    if sub is None or tax is None or total is None:
        problems.append(Problem.TOTALS_MISSING)
    else:
        if total <= 0 or sub <= 0:
            problems.append(Problem.NOT_A_BILL)
        if sub + tax != total or (
            len(lines) == len(facts.lines) and sum(ln.amount_minor for ln in lines) != sub
        ):
            problems.append(Problem.TOTALS_MISMATCH)
        elif len(lines) == len(facts.lines) and tax_sum != tax:
            problems.append(Problem.TAX_MISMATCH)

    if problems or facts.invoice_date is None or facts.currency is None:
        return MapResult(None, tuple(dict.fromkeys(problems)))
    assert sub is not None and tax is not None and total is not None  # no TOTALS_MISSING above
    return MapResult(
        BillDraft(
            contact_id=contact_id, invoice_number=number, invoice_date=facts.invoice_date,
            due_date=facts.due_date, currency=facts.currency, subtotal_minor=sub, tax_minor=tax,
            total_minor=total, lines=tuple(lines),
        ),
        (),
    )  # fmt: skip


# ---- the supplier's contact in the accounting system ---------------------------------------------


@dataclass(frozen=True)
class ContactCandidate:
    contact_id: str
    name: str
    tax_number: str | None
    active: bool = True


@dataclass(frozen=True)
class ContactDecision:
    contact_id: str | None
    matched_by: str | None  # "link", "tax_id" or "name"
    problem: Problem | None = None


def _name_key(name: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", name.casefold()).split())


def _tax_key(tax_id: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", tax_id or "").upper()


def _no(problem: Problem) -> ContactDecision:
    return ContactDecision(None, None, problem)


def match_contact(
    supplier_name: str,
    supplier_tax_id: str | None,
    linked_contact_id: str | None,
    candidates: Sequence[ContactCandidate],
) -> ContactDecision:
    """A stored link, then the tax number (ABN/NZBN), then an exact name. Never the closest name;
    never one of several; and never a contact whose tax number contradicts the supplier's."""
    live = [c for c in candidates if c.active]
    want_tax = _tax_key(supplier_tax_id)

    if linked_contact_id is not None:
        linked = next((c for c in live if c.contact_id == linked_contact_id), None)
        if linked is None:
            return _no(Problem.CONTACT_NOT_FOUND)
        if want_tax and _tax_key(linked.tax_number) not in ("", want_tax):
            return _no(Problem.CONTACT_CONFLICT)
        return ContactDecision(linked.contact_id, "link")

    if want_tax:
        by_tax = [c for c in live if _tax_key(c.tax_number) == want_tax]
        if len(by_tax) > 1:
            return _no(Problem.CONTACT_AMBIGUOUS)
        if by_tax:
            return ContactDecision(by_tax[0].contact_id, "tax_id")

    key = _name_key(supplier_name)
    by_name = [c for c in live if key and _name_key(c.name) == key]
    if len(by_name) > 1:
        return _no(Problem.CONTACT_AMBIGUOUS)
    if not by_name:
        return _no(Problem.CONTACT_NOT_FOUND)
    if want_tax and _tax_key(by_name[0].tax_number) not in ("", want_tax):
        return _no(Problem.CONTACT_CONFLICT)
    return ContactDecision(by_name[0].contact_id, "name")


# ---- checking what the provider says it created --------------------------------------------------


@dataclass(frozen=True)
class ReturnedBill:
    bill_id: str
    status: str
    contact_id: str
    currency: str
    subtotal_minor: int
    tax_minor: int
    total_minor: int


def verify_created(sent: BillDraft, got: ReturnedBill) -> Problem | None:
    """Success means the bill exists, is still a draft, and says what was sent. A bill in any other
    status is critical: it is recorded and left alone (no corrective approval or payment call)."""
    if got.status != DRAFT:
        return Problem.UNEXPECTED_BILL_STATUS
    same = (
        bool(got.bill_id)
        and got.contact_id == sent.contact_id
        and got.currency == sent.currency
        and (got.subtotal_minor, got.tax_minor, got.total_minor)
        == (sent.subtotal_minor, sent.tax_minor, sent.total_minor)
    )
    return None if same else Problem.RESPONSE_MISMATCH


# ---- what to do when a call fails ----------------------------------------------------------------


class Failure(StrEnum):
    CONNECT_FAILED = "connect_failed"  # never reached the provider
    RATE_LIMITED = "rate_limited"  # the provider refused it unprocessed (429)
    UNAUTHORISED = "unauthorised"
    FORBIDDEN = "forbidden"
    REJECTED = "rejected"  # the provider found the bill invalid (400)
    TIMEOUT = "timeout"  # sent; no answer
    CONNECTION_LOST = "connection_lost"  # sent; the connection dropped
    SERVER_ERROR = "server_error"  # 5xx: it may or may not have acted
    BAD_RESPONSE = "bad_response"  # an answer we cannot read
    CONFLICT = "conflict"  # 409: something may already exist
    UNEXPECTED_STATUS = "unexpected_status"  # created, but not as a draft


class Next(StrEnum):
    RETRY = "retry"
    FAIL = "fail"
    UNKNOWN = "unknown"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Decision:
    next: Next
    delay_s: int = 0
    manual_action: bool = False


_NOT_PROCESSED = frozenset({Failure.CONNECT_FAILED, Failure.RATE_LIMITED})
_MAY_HAVE_WRITTEN = frozenset(
    {Failure.TIMEOUT, Failure.CONNECTION_LOST, Failure.SERVER_ERROR, Failure.BAD_RESPONSE,
     Failure.CONFLICT}
)  # fmt: skip


def decide_after_failure(failure: Failure, attempt: int, retry_after_s: int | None) -> Decision:
    """Retry only what the provider cannot have acted on, a few times, obeying Retry-After. A
    failure after the request may have been received is an unknown outcome, never a retry."""
    if failure in _NOT_PROCESSED:
        if attempt >= MAX_ATTEMPTS:
            return Decision(Next.FAIL, manual_action=True)
        if retry_after_s is not None:
            if retry_after_s > RETRY_AFTER_MAX_S:
                return Decision(Next.FAIL, manual_action=True)
            return Decision(Next.RETRY, delay_s=max(retry_after_s, 1))
        return Decision(Next.RETRY, delay_s=min(2**attempt, 30))
    if failure in _MAY_HAVE_WRITTEN:
        return Decision(Next.UNKNOWN, manual_action=True)
    if failure is Failure.UNEXPECTED_STATUS:
        return Decision(Next.CRITICAL, manual_action=True)
    return Decision(Next.FAIL, manual_action=True)  # unauthorised, forbidden, rejected


# ---- what to tell the person ---------------------------------------------------------------------

_PROBLEM_TEXT: dict[str, str] = {
    "NOT_APPROVED": "Only an invoice a reviewer has approved can be sent to the accounting system.",
    "ALREADY_HANDED_OFF": "This invoice already has a draft bill in the accounting system.",
    "HANDOFF_IN_PROGRESS": "A handoff for this invoice is already running. Wait for it to finish.",
    "OUTCOME_UNKNOWN": "We could not tell whether the draft bill was created. Check the accounting "
    "system, then record what you found.",
    "INVOICE_NUMBER_MISSING": "The invoice has no invoice number. Add it in the review screen.",
    "INVOICE_DATE_MISSING": "The invoice has no invoice date. Add it in the review screen.",
    "CURRENCY_UNSUPPORTED": "The invoice currency is missing or not supported.",
    "NO_LINES": "The invoice has no line items to put on a bill.",
    "LINE_DESCRIPTION_MISSING": "A line item has no description.",
    "LINE_AMOUNT_MISSING": "A line item has no amount.",
    "TOTALS_MISSING": "The subtotal, tax or total is missing.",
    "TOTALS_MISMATCH": "The line items do not add up to the invoice totals. Nothing was changed.",
    "TAX_MAPPING_MISSING": "A line has a tax rate with no approved accounting tax type. Ask an "
    "administrator to map it; it is never guessed.",
    "TAX_MISMATCH": "The tax on the lines does not equal the invoice tax, so the accounting system "
    "would calculate a different total. Nothing was changed.",
    "NOT_A_BILL": "Credit notes and zero-value invoices are not sent in this version.",
    "CONTACT_NOT_FOUND": "No matching supplier was found in the accounting system. Create or link "
    "the contact there first.",
    "CONTACT_AMBIGUOUS": "More than one supplier in the accounting system matches. Link the right "
    "one; this is never chosen automatically.",
    "CONTACT_CONFLICT": "A supplier matched by name has a different tax number (ABN/NZBN) "
    "than ours.",
    "BILL_ALREADY_EXISTS": "The accounting system already has a bill from this supplier with this "
    "invoice number. Nothing was created.",
    "WORKER_INTERRUPTED": "The handoff stopped part-way, so it may or may not have created a bill.",
    "UNEXPECTED_BILL_STATUS": "The accounting system returned a bill that is not a draft. It was "
    "left as it is; check it there.",
    "RESPONSE_MISMATCH": "The bill the accounting system created differs from what was sent. Check "
    "it there.",
}
_FAILURE_TEXT: dict[str, str] = {
    "connect_failed": "The accounting system could not be reached. Nothing was created.",
    "rate_limited": "The accounting system asked us to slow down. Nothing was created.",
    "unauthorised": "The connection to the accounting system is no longer authorised. Reconnect "
    "it.",
    "forbidden": "The connection does not have permission to do this.",
    "rejected": "The accounting system refused the bill as invalid. Nothing was created.",
    "timeout": "The accounting system did not answer in time. The bill may have been created.",
    "connection_lost": "The connection dropped. The bill may have been created.",
    "server_error": "The accounting system reported an error. The bill may have been created.",
    "bad_response": "The answer could not be understood. The bill may have been created.",
    "conflict": "The accounting system reported a conflict. The bill may have been created.",
    "unexpected_status": "The accounting system returned a bill that is not a draft.",
}
EXPLANATIONS: dict[str, str] = {**_PROBLEM_TEXT, **_FAILURE_TEXT}


def explain(status: HandoffStatus, code: str | None) -> str:
    """One plain sentence for the screen. Success is only claimed when it is proven."""
    if status is HandoffStatus.SUCCEEDED:
        return "A draft bill was created. Review and approve it in the accounting system."
    if status is HandoffStatus.PENDING or status is HandoffStatus.IN_PROGRESS:
        return "Sending the draft bill to the accounting system."
    if code is None:
        return "The handoff did not complete."
    if code.startswith("INTERNAL_ERROR"):
        return "Something unexpected stopped the handoff before anything was sent."
    return EXPLANATIONS.get(code, "The handoff did not complete.")
