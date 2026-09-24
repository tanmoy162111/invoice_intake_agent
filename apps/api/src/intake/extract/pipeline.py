"""Stage 2-3 of the pipeline: extract with the model, normalize, score, and store.

Idempotent: an invoice past `extracting` is left alone, and the model answer is cached, so running
the job twice neither pays twice nor writes twice. Every status change writes an audit event.
"""

import json
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from intake.audit.writer import record_event
from intake.config import Settings
from intake.core.confidence import CRITICAL_FIELDS, fields_below_threshold
from intake.core.extraction import MasterData, RawField, RawInvoice, interpret
from intake.core.llm_budget import budget_violation, estimate_input_tokens, skip_reason
from intake.core.normalize import normalize_supplier_name
from intake.core.statuses import ActorType, InvoiceStatus
from intake.db.invoices import set_invoice_status
from intake.db.models import Document, FieldExtraction, Invoice, InvoiceLine, Supplier
from intake.extract.llm import LlmClient, PageInput
from intake.extract.pages import fit_for_model, png_size
from intake.extract.prompt import load_prompt
from intake.extract.schema import ExtractedField, InvoiceExtraction
from intake.extract.service import ExtractionFailed, run_extraction
from intake.ingest.storage import LocalStorage
from intake.security import BankVault

EXTRACT_JOB = "extract_invoice"
_STARTABLE = {InvoiceStatus.RECEIVED, InvoiceStatus.EXTRACTING, InvoiceStatus.FAILED}
_BANK_FIELD = "supplier_bank_account"


def _raw(field: ExtractedField) -> RawField:
    return RawField(field.value, field.self_confidence, field.page)


def to_raw_invoice(extraction: InvoiceExtraction) -> RawInvoice:
    header = {
        name: _raw(getattr(extraction, name))
        for name in InvoiceExtraction.model_fields
        if name != "lines"
    }
    lines = [{name: _raw(getattr(line, name)) for name in type(line).model_fields}
             for line in extraction.lines]  # fmt: skip
    return RawInvoice(header, lines)


def _master(session: Session, tenant_id: uuid.UUID, raw: RawInvoice) -> MasterData:
    """Is the supplier already in master data (by tax id or by name/alias)? Absence isn't an
    error here; UNKNOWN_SUPPLIER is a validation rule (M4)."""
    tax = (raw.header["supplier_tax_id"].value or "").strip().upper()
    name = raw.header["supplier_name"].value
    wanted = normalize_supplier_name(name) if name and name.strip() else None
    tax_known = name_known = False
    for s in session.execute(select(Supplier).where(Supplier.tenant_id == tenant_id)).scalars():
        if tax and s.tax_id and s.tax_id.strip().upper() == tax:
            tax_known = True
        if wanted and wanted in {normalize_supplier_name(n) for n in [s.name, *s.aliases]}:
            name_known = True
    return MasterData(tax_id_known=tax_known, name_known=name_known)


def _fail(session: Session, inv: Invoice, reason: str) -> None:
    set_invoice_status(
        session, inv, InvoiceStatus.FAILED, actor_type=ActorType.SYSTEM, actor_id="worker",
        reason=reason,
    )  # fmt: skip
    record_event(
        session, tenant_id=inv.tenant_id, invoice_id=inv.id, event_type="extraction_failed",
        actor_type=ActorType.SYSTEM, actor_id="worker", data={"reason": reason},
    )  # fmt: skip


def _load_pages(
    storage: LocalStorage, doc: Document
) -> tuple[list[PageInput], list[str], list[tuple[int, int]]]:
    base = storage.pages_rel(doc.tenant_id, doc.id)
    texts: list[str] = json.loads(storage.read(f"{base}/text.json"))
    pages: list[PageInput] = []
    sizes: list[tuple[int, int]] = []
    for n in range(1, len(texts) + 1):
        png = storage.read(f"{base}/page-{n:03d}.png")
        sizes.append(png_size(png))
        data, media = fit_for_model(png)
        pages.append(PageInput(data, media))
    return pages, texts, sizes


def extract_invoice(
    session: Session,
    storage: LocalStorage,
    settings: Settings,
    client: LlmClient,
    vault: BankVault,
    invoice_id: uuid.UUID,
) -> None:
    inv = session.get_one(Invoice, invoice_id)
    if InvoiceStatus(inv.status) not in _STARTABLE:
        return  # already extracted (or further along): nothing to do
    doc = session.get_one(Document, inv.document_id)
    if InvoiceStatus(inv.status) is not InvoiceStatus.EXTRACTING:
        set_invoice_status(
            session, inv, InvoiceStatus.EXTRACTING, actor_type=ActorType.SYSTEM, actor_id="worker"
        )

    reason = skip_reason(doc.doc_quality)
    if reason:
        return _fail(session, inv, reason)
    pages, texts, sizes = _load_pages(storage, doc)
    reason = budget_violation(
        pages=len(pages),
        est_input_tokens=estimate_input_tokens(sizes, sum(len(t) for t in texts)),
        max_pages=settings.max_pages,
        max_input_tokens=settings.extract_max_input_tokens,
    )
    if reason:
        return _fail(session, inv, reason)

    try:
        out = run_extraction(
            session, client, vault, settings, tenant_id=inv.tenant_id, invoice_id=inv.id,
            file_sha256=doc.file_sha256,
            system_prompt=load_prompt(settings.extraction_prompt_version),
            pages=pages, text_layers=texts,
        )  # fmt: skip
    except ExtractionFailed as exc:
        return _fail(session, inv, exc.reason)

    raw = to_raw_invoice(out.extraction)
    master = _master(session, inv.tenant_id, raw)
    interpreted = interpret(raw, doc_quality=doc.doc_quality, page_texts=texts, master=master)

    inv.supplier_name = interpreted.supplier_name
    inv.invoice_number = interpreted.invoice_number
    inv.invoice_date = interpreted.invoice_date
    inv.due_date = interpreted.due_date
    inv.po_number = interpreted.po_number
    inv.payment_terms = interpreted.payment_terms
    inv.subtotal_minor = interpreted.subtotal_minor
    inv.tax_minor = interpreted.tax_minor
    inv.total_minor = interpreted.total_minor
    inv.currency = interpreted.currency
    for line in interpreted.lines:
        session.add(
            InvoiceLine(
                tenant_id=inv.tenant_id, invoice_id=inv.id, line_no=line.line_no,
                description=line.description, sku=line.sku, qty=line.quantity,
                unit_price_minor=line.unit_price_minor, amount_minor=line.amount_minor,
                tax_rate=line.tax_rate,
            )
        )  # fmt: skip
    for f in interpreted.fields:
        raw_value, normalized = f.raw, f.normalized
        if f.field == _BANK_FIELD and raw_value is not None:
            # Bank details are never stored in the clear: encrypted at rest, keyed hash to compare.
            raw_value = vault.encrypt(raw_value)
            normalized = vault.hash(f.raw) if f.raw else None
        session.add(
            FieldExtraction(
                tenant_id=inv.tenant_id, invoice_id=inv.id, field=f.field, raw_value=raw_value,
                normalized_value=normalized, confidence=f.confidence.quantize(Decimal("0.0001")),
                signals=f.signals, page=f.page,
            )
        )  # fmt: skip

    scores = {f.field: f.confidence for f in interpreted.fields}
    weak = fields_below_threshold(scores, CRITICAL_FIELDS, settings.field_confidence_min)
    set_invoice_status(
        session, inv, InvoiceStatus.EXTRACTED, actor_type=ActorType.SYSTEM, actor_id="worker"
    )
    record_event(
        session, tenant_id=inv.tenant_id, invoice_id=inv.id, event_type="extraction_completed",
        actor_type=ActorType.AGENT, actor_id=client.model,
        data={
            "prompt_version": settings.extraction_prompt_version,
            "cached": out.cached, "model_calls": out.calls, "cost_usd_micros": out.cost_micros,
            "fields": len(interpreted.fields), "lines": len(interpreted.lines),
            "low_confidence_critical_fields": weak,
        },
    )  # fmt: skip
