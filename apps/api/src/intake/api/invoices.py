"""The reviewer's queue and the invoice screen (playbook M8): read the queue, read one invoice with
its document pages, and act on it. Every action returns the invoice as it now stands, so a screen
can show the re-checked result at once."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from intake.api.deps import require_user, session_dep, settings_dep, storage_dep
from intake.config import Settings
from intake.core.exceptions import ExceptionCode, Severity
from intake.core.review import ExceptionState, ReviewProblem, approval_problem, confidence_reason
from intake.core.statuses import ExceptionStatus, InvoiceStatus, ReviewAction
from intake.db.models import (
    AuditEvent,
    CheckResult,
    Document,
    FieldExtraction,
    Invoice,
    InvoiceException,
    InvoiceLine,
    ReviewActionRow,
    Supplier,
)
from intake.ingest.storage import LocalStorage
from intake.review import service
from intake.security import BankVault

router = APIRouter(prefix="/invoices", tags=["invoices"])
exceptions_router = APIRouter(prefix="/exceptions", tags=["invoices"])

_BANK_FIELD = "supplier_bank_account"
_QUEUE_DEFAULT = [InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.FAILED]
_RANK = {Severity.BLOCK.value: 3, Severity.REVIEW.value: 2, Severity.INFO.value: 1}
_MESSAGES = {
    ReviewProblem.WRONG_STATUS: "This invoice is not in a state where that can be done.",
    ReviewProblem.OPEN_EXCEPTIONS: "Close every exception before approving.",
    ReviewProblem.NOTE_REQUIRED: "A note is required.",
    ReviewProblem.NOTE_TOO_LONG: "The note is too long.",
    ReviewProblem.UNKNOWN_FIELD: "That field cannot be corrected.",
    ReviewProblem.VALUE_REQUIRED: "That field cannot be left empty.",
    ReviewProblem.INVALID_VALUE: "That value could not be read for this field.",
    ReviewProblem.CURRENCY_NEEDED: "Set the currency first; amounts are read in it.",
    ReviewProblem.CURRENCY_HAS_AMOUNTS: (
        "Amounts were already read in the current currency and cannot be re-read. "
        "Reject the invoice and ask for a corrected copy."
    ),
}


# ---- what the API returns ---------------------------------------------------------------------


class ExceptionRef(BaseModel):
    code: ExceptionCode
    severity: Severity


class QueueItem(BaseModel):
    id: uuid.UUID
    status: InvoiceStatus
    route: str | None
    supplier_id: uuid.UUID | None
    supplier_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    total_minor: int | None
    currency: str | None
    doc_quality: str | None
    created_at: datetime
    open_exceptions: int
    top_exception: ExceptionRef | None
    info_requested: bool


class QueueOut(BaseModel):
    items: list[QueueItem]
    total: int


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    page_count: int | None
    doc_quality: str


class FieldOut(BaseModel):
    field: str
    raw: str | None  # as printed (a bank account is masked)
    value: str | None  # normalized (amounts are minor units; a bank account is masked)
    confidence: float | None
    weak: bool
    reason: str | None
    corrected: bool
    corrected_by: str | None
    page: int | None


class LineOut(BaseModel):
    line_no: int
    description: str | None
    sku: str | None
    qty: str | None
    unit_price_minor: int | None
    amount_minor: int | None
    matched: bool


class RelatedInvoice(BaseModel):
    id: uuid.UUID
    supplier_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    total_minor: int | None
    currency: str | None
    status: InvoiceStatus


class ExceptionOut(BaseModel):
    id: uuid.UUID
    code: ExceptionCode
    severity: Severity
    status: ExceptionStatus
    explanation: str
    suggested_fix: str
    resolved_by: str | None
    resolution_note: str | None
    created_at: datetime
    related_invoice: RelatedInvoice | None


class CheckOut(BaseModel):
    code: str
    outcome: str


class BankOut(BaseModel):
    present: bool
    masked: str | None


class SupplierOut(BaseModel):
    id: uuid.UUID
    name: str


class InvoiceDetail(BaseModel):
    id: uuid.UUID
    status: InvoiceStatus
    route: str | None
    created_at: datetime
    header: dict[str, Any]
    supplier: SupplierOut | None
    document: DocumentOut
    fields: list[FieldOut]
    lines: list[LineOut]
    exceptions: list[ExceptionOut]
    checks: list[CheckOut]
    routing_reasons: list[str]
    info_requested: bool
    can_approve: bool
    approval_blockers: int
    bank: BankOut


class CorrectionIn(BaseModel):
    field: str = Field(max_length=100)
    value: str = Field(max_length=2000)


class CloseIn(BaseModel):
    resolution: Literal["resolved", "dismissed"]
    note: str | None = Field(default=None, max_length=5000)


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=5000)


class RejectIn(BaseModel):
    reason: str = Field(max_length=5000)


class RevealOut(BaseModel):
    account: str


# ---- helpers ----------------------------------------------------------------------------------


def _vault(settings: Settings) -> BankVault | None:
    try:
        return BankVault(settings.bank_encryption_key)
    except ValueError:
        return None


def _masked(vault: BankVault | None, token: str | None) -> str | None:
    if not token:
        return None
    if vault is None:
        return "••••"
    try:
        return vault.mask(vault.decrypt(token))
    except ValueError:  # a token that cannot be decrypted with this key
        return "••••"
    except Exception:  # noqa: BLE001 - cryptography raises its own InvalidToken
        return "••••"


def _tenant(settings: Settings) -> uuid.UUID:
    return uuid.UUID(settings.default_tenant_id)


def _load(session: Session, tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    inv = session.get(Invoice, invoice_id)
    if inv is None or inv.tenant_id != tenant_id:
        raise HTTPException(404, "invoice not found")
    return inv


def _refused(error: service.ReviewRefused) -> HTTPException:
    problem = error.problem
    conflict = problem in (ReviewProblem.WRONG_STATUS, ReviewProblem.OPEN_EXCEPTIONS)
    return HTTPException(
        409 if conflict else 422, {"code": problem.value, "message": _MESSAGES[problem]}
    )


def _detail(
    session: Session, settings: Settings, tenant_id: uuid.UUID, invoice_id: uuid.UUID
) -> InvoiceDetail:
    inv = _load(session, tenant_id, invoice_id)
    doc = session.get_one(Document, inv.document_id)
    vault = _vault(settings)
    minimum = settings.field_confidence_min
    fields: list[FieldOut] = []
    bank_token: str | None = None
    for f in session.execute(
        select(FieldExtraction)
        .where(FieldExtraction.tenant_id == tenant_id, FieldExtraction.invoice_id == inv.id)
        .order_by(FieldExtraction.field)
    ).scalars():
        corrected = f.corrected_value is not None or (f.signals or {}).get("corrected") is True
        if f.field == _BANK_FIELD:
            bank_token = f.raw_value
            shown = _masked(vault, f.raw_value)
            raw, value = shown, shown
        else:
            raw, value = f.raw_value, f.normalized_value
        reason = None if corrected else confidence_reason(f.confidence, f.signals or {}, minimum)
        fields.append(
            FieldOut(
                field=f.field, raw=raw, value=value,
                confidence=float(f.confidence) if f.confidence is not None else None,
                weak=reason is not None, reason=reason, corrected=corrected,
                corrected_by=f.corrected_by, page=f.page,
            )
        )  # fmt: skip
    lines = [
        LineOut(
            line_no=ln.line_no, description=ln.description, sku=ln.sku,
            qty=str(ln.qty.normalize()) if isinstance(ln.qty, Decimal) else None,
            unit_price_minor=ln.unit_price_minor, amount_minor=ln.amount_minor,
            matched=ln.matched_po_line_id is not None,
        )
        for ln in session.execute(
            select(InvoiceLine)
            .where(InvoiceLine.tenant_id == tenant_id, InvoiceLine.invoice_id == inv.id)
            .order_by(InvoiceLine.line_no)
        ).scalars()
    ]  # fmt: skip
    checks_rows = list(
        session.execute(
            select(CheckResult).where(
                CheckResult.tenant_id == tenant_id, CheckResult.invoice_id == inv.id
            )
        ).scalars()
    )
    duplicate_of = next(
        (
            c.details.get("existing_invoice_id")
            for c in checks_rows
            if c.check_code == "POSSIBLE_DUPLICATE" and c.details.get("outcome") == "fail"
        ),
        None,
    )
    exceptions = sorted(
        session.execute(
            select(InvoiceException).where(
                InvoiceException.tenant_id == tenant_id, InvoiceException.invoice_id == inv.id
            )
        ).scalars(),
        key=lambda e: (e.status != "open", -_RANK.get(e.severity, 0), e.created_at, e.code),
    )
    related: RelatedInvoice | None = None
    if duplicate_of:
        other = session.get(Invoice, uuid.UUID(str(duplicate_of)))
        if other is not None and other.tenant_id == tenant_id:
            related = RelatedInvoice(
                id=other.id, supplier_name=other.supplier_name, invoice_number=other.invoice_number,
                invoice_date=other.invoice_date, total_minor=other.total_minor,
                currency=other.currency, status=InvoiceStatus(other.status),
            )  # fmt: skip
    routed = session.execute(
        select(AuditEvent)
        .where(
            AuditEvent.tenant_id == tenant_id, AuditEvent.invoice_id == inv.id,
            AuditEvent.event_type == "routing_decided",
        )
        .order_by(AuditEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()  # fmt: skip
    supplier = session.get(Supplier, inv.supplier_id) if inv.supplier_id else None
    open_count = sum(1 for e in exceptions if e.status == ExceptionStatus.OPEN.value)
    states = [
        ExceptionState(ExceptionCode(e.code), Severity(e.severity), ExceptionStatus(e.status))
        for e in exceptions
    ]
    masked = _masked(vault, bank_token)
    header = {
        "supplier_name": inv.supplier_name, "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date, "due_date": inv.due_date, "po_number": inv.po_number,
        "payment_terms": inv.payment_terms, "subtotal_minor": inv.subtotal_minor,
        "tax_minor": inv.tax_minor, "total_minor": inv.total_minor, "currency": inv.currency,
    }  # fmt: skip
    return InvoiceDetail(
        id=inv.id, status=InvoiceStatus(inv.status), route=inv.route, created_at=inv.created_at,
        header=header,
        supplier=SupplierOut(id=supplier.id, name=supplier.name) if supplier else None,
        document=DocumentOut(
            id=doc.id, filename=doc.filename, page_count=doc.page_count, doc_quality=doc.doc_quality
        ),
        fields=fields, lines=lines,
        exceptions=[
            ExceptionOut(
                id=e.id, code=ExceptionCode(e.code), severity=Severity(e.severity),
                status=ExceptionStatus(e.status), explanation=e.explanation,
                suggested_fix=e.suggested_fix, resolved_by=e.resolved_by,
                resolution_note=e.resolution_note, created_at=e.created_at,
                related_invoice=(
                    related if e.code == ExceptionCode.POSSIBLE_DUPLICATE.value else None
                ),
            )
            for e in exceptions
        ],
        checks=[
            CheckOut(code=c.check_code, outcome=str(c.details.get("outcome", "")))
            for c in sorted(checks_rows, key=lambda c: c.check_code)
        ],
        routing_reasons=list(routed.data.get("reasons", [])) if routed else [],
        info_requested=_info_requested(session, tenant_id, inv.id),
        can_approve=approval_problem(InvoiceStatus(inv.status), states) is None,
        approval_blockers=open_count,
        bank=BankOut(present=masked is not None, masked=masked),
    )  # fmt: skip


def _info_requested(session: Session, tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> bool:
    return (
        session.scalar(
            select(func.count()).select_from(ReviewActionRow).where(
                ReviewActionRow.tenant_id == tenant_id,
                ReviewActionRow.invoice_id == invoice_id,
                ReviewActionRow.action == ReviewAction.REQUEST_INFO.value,
            )
        )
        or 0
    ) > 0  # fmt: skip


# ---- the queue --------------------------------------------------------------------------------


@router.get("", response_model=QueueOut)
def queue(
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    status: Annotated[list[InvoiceStatus] | None, Query()] = None,
    code: ExceptionCode | None = None,
    supplier_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QueueOut:
    """Invoices that need a person: worst exception first, then oldest. `status` repeats
    (default: needs_review and failed); `code` keeps invoices with that exception still open."""
    tenant_id = _tenant(settings)
    wanted = [s.value for s in (status or _QUEUE_DEFAULT)]
    open_exc = (InvoiceException.invoice_id == Invoice.id) & (InvoiceException.status == "open")
    rank = case(
        (InvoiceException.severity == Severity.BLOCK.value, 3),
        (InvoiceException.severity == Severity.REVIEW.value, 2),
        else_=1,
    )
    worst = (
        select(func.coalesce(func.max(rank), 0))
        .where(open_exc)
        .correlate(Invoice)
        .scalar_subquery()
    )
    where = [Invoice.tenant_id == tenant_id, Invoice.status.in_(wanted)]
    if supplier_id is not None:
        where.append(Invoice.supplier_id == supplier_id)
    if code is not None:
        where.append(
            select(InvoiceException.id)
            .where(open_exc, InvoiceException.code == code.value)
            .exists()
        )
    total = session.scalar(select(func.count()).select_from(Invoice).where(*where)) or 0
    rows = session.execute(
        select(Invoice, Document.doc_quality)
        .join(Document, Document.id == Invoice.document_id)
        .where(*where)
        .order_by(worst.desc(), Invoice.created_at, Invoice.id)
        .limit(limit)
        .offset(offset)
    ).all()
    ids = [inv.id for inv, _ in rows]
    open_by_invoice: dict[uuid.UUID, list[InvoiceException]] = {i: [] for i in ids}
    for e in session.execute(
        select(InvoiceException).where(
            InvoiceException.tenant_id == tenant_id,
            InvoiceException.invoice_id.in_(ids),
            InvoiceException.status == "open",
        )
    ).scalars():
        open_by_invoice[e.invoice_id].append(e)
    asked = set(
        session.scalars(
            select(ReviewActionRow.invoice_id).where(
                ReviewActionRow.tenant_id == tenant_id,
                ReviewActionRow.invoice_id.in_(ids),
                ReviewActionRow.action == ReviewAction.REQUEST_INFO.value,
            )
        )
    )
    items = []
    for inv, quality in rows:
        opened = sorted(
            open_by_invoice[inv.id], key=lambda e: (-_RANK.get(e.severity, 0), e.created_at, e.code)
        )
        items.append(
            QueueItem(
                id=inv.id, status=InvoiceStatus(inv.status), route=inv.route,
                supplier_id=inv.supplier_id, supplier_name=inv.supplier_name,
                invoice_number=inv.invoice_number, invoice_date=inv.invoice_date,
                total_minor=inv.total_minor, currency=inv.currency, doc_quality=quality,
                created_at=inv.created_at, open_exceptions=len(opened),
                top_exception=(
                    ExceptionRef(
                        code=ExceptionCode(opened[0].code), severity=Severity(opened[0].severity)
                    )
                    if opened
                    else None
                ),
                info_requested=inv.id in asked,
            )
        )  # fmt: skip
    return QueueOut(items=items, total=total)


# ---- one invoice ------------------------------------------------------------------------------


@router.get("/{invoice_id}", response_model=InvoiceDetail)
def invoice_detail(
    invoice_id: uuid.UUID,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> InvoiceDetail:
    return _detail(session, settings, _tenant(settings), invoice_id)


@router.get(
    "/{invoice_id}/pages/{page}",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def page_image(
    invoice_id: uuid.UUID,
    page: int,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    storage: Annotated[LocalStorage, Depends(storage_dep)],
) -> Response:
    tenant_id = _tenant(settings)
    inv = _load(session, tenant_id, invoice_id)
    doc = session.get_one(Document, inv.document_id)
    if page < 1 or doc.page_count is None or page > doc.page_count:
        raise HTTPException(404, "page not found")
    try:
        png = storage.read(f"{storage.pages_rel(doc.tenant_id, doc.id)}/page-{page:03d}.png")
    except OSError:  # missing, or not a readable file
        raise HTTPException(404, "page not found") from None
    return Response(
        png, media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )  # fmt: skip


# ---- acting -----------------------------------------------------------------------------------


def _act(session: Session, settings: Settings, invoice_id: uuid.UUID, do: Any) -> InvoiceDetail:
    tenant_id = _tenant(settings)
    try:
        do(tenant_id)
        session.commit()
    except service.NotFound:
        session.rollback()
        raise HTTPException(404, "not found") from None
    except service.ReviewRefused as error:
        session.rollback()
        raise _refused(error) from None
    session.expire_all()
    return _detail(session, settings, tenant_id, invoice_id)


@router.post("/{invoice_id}/corrections", response_model=InvoiceDetail)
def correct(
    invoice_id: uuid.UUID,
    body: CorrectionIn,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    actor: Annotated[str, Depends(require_user)],
) -> InvoiceDetail:
    """Correct a header field. The checks and the routing run again straight away."""
    return _act(
        session, settings, invoice_id,
        lambda t: service.correct_field(
            session, settings, tenant_id=t, invoice_id=invoice_id, actor=actor,
            field=body.field, value=body.value,
        ),
    )  # fmt: skip


@router.post("/{invoice_id}/approve", response_model=InvoiceDetail)
def approve(
    invoice_id: uuid.UUID,
    body: NoteIn,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    actor: Annotated[str, Depends(require_user)],
) -> InvoiceDetail:
    return _act(
        session, settings, invoice_id,
        lambda t: service.approve_invoice(
            session, tenant_id=t, invoice_id=invoice_id, actor=actor, note=body.note
        ),
    )  # fmt: skip


@router.post("/{invoice_id}/reject", response_model=InvoiceDetail)
def reject(
    invoice_id: uuid.UUID,
    body: RejectIn,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    actor: Annotated[str, Depends(require_user)],
) -> InvoiceDetail:
    return _act(
        session, settings, invoice_id,
        lambda t: service.reject_invoice(
            session, tenant_id=t, invoice_id=invoice_id, actor=actor, reason=body.reason
        ),
    )  # fmt: skip


@router.post("/{invoice_id}/request-info", response_model=InvoiceDetail)
def request_information(
    invoice_id: uuid.UUID,
    body: NoteIn,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    actor: Annotated[str, Depends(require_user)],
) -> InvoiceDetail:
    return _act(
        session, settings, invoice_id,
        lambda t: service.request_info(
            session, tenant_id=t, invoice_id=invoice_id, actor=actor, note=body.note
        ),
    )  # fmt: skip


@router.post("/{invoice_id}/bank/reveal", response_model=RevealOut)
def reveal_bank(
    invoice_id: uuid.UUID,
    response: Response,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    actor: Annotated[str, Depends(require_user)],
) -> RevealOut:
    """Show the account as read from the invoice. Revealing it is written to the history."""
    vault = _vault(settings)
    if vault is None:
        raise HTTPException(503, "BANK_ENCRYPTION_KEY is not configured on the server")
    try:
        account = service.reveal_bank_account(
            session, vault, tenant_id=_tenant(settings), invoice_id=invoice_id, actor=actor
        )
        session.commit()
    except service.NotFound:
        session.rollback()
        raise HTTPException(404, "no bank account on this invoice") from None
    except service.Unreadable:
        session.commit()  # keep the record of the failed attempt
        raise HTTPException(
            409,
            {
                "code": "BANK_UNREADABLE",
                "message": "The stored account cannot be read with the configured key.",
            },
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return RevealOut(account=account)


@exceptions_router.post("/{exception_id}/close", response_model=InvoiceDetail)
def close_exception(
    exception_id: uuid.UUID,
    body: CloseIn,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    actor: Annotated[str, Depends(require_user)],
) -> InvoiceDetail:
    """Resolve or dismiss an exception. A block exception needs a note."""
    tenant_id = _tenant(settings)
    ex = session.get(InvoiceException, exception_id)
    if ex is None or ex.tenant_id != tenant_id:
        raise HTTPException(404, "exception not found")
    return _act(
        session, settings, ex.invoice_id,
        lambda t: service.close_exception(
            session, tenant_id=t, exception_id=exception_id, actor=actor,
            resolution=body.resolution, note=body.note,
        ),
    )  # fmt: skip
