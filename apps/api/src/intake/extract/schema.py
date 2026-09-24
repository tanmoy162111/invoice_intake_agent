"""`InvoiceExtraction` (playbook §5.3): what the model returns for every invoice.

Every field carries a value (a string, or null when absent), the model's own confidence, and the
1-based page it was read from. The JSON schema generated here is used as a strict tool's
`input_schema`, so it sticks to the supported subset: `additionalProperties: false` everywhere,
nullable via `anyOf`, and no numeric or string constraints (those are checked in Python below).
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from intake.core.confidence import SelfConfidence

MAX_VALUE_CHARS = 2000  # no real invoice field is longer; bounds what a hostile document can store

HEADER_FIELDS: tuple[str, ...] = (
    "supplier_name",
    "supplier_tax_id",
    "supplier_address",
    "supplier_bank_account",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "currency",
    "subtotal",
    "tax_total",
    "total",
    "payment_terms",
)
LINE_FIELDS: tuple[str, ...] = (
    "description",
    "sku",
    "quantity",
    "unit_price",
    "amount",
    "tax_rate",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedField(_Strict):
    value: str | None
    self_confidence: SelfConfidence
    page: int | None

    @field_validator("value")
    @classmethod
    def _storable(cls, value: str | None) -> str | None:
        if value is not None:
            if "\x00" in value:  # Postgres cannot store NUL in text or JSONB
                raise ValueError("contains a NUL character")
            if len(value) > MAX_VALUE_CHARS:
                raise ValueError("value is too long")
        return value

    @field_validator("page")
    @classmethod
    def _one_based(cls, page: int | None) -> int | None:
        if page is not None and page < 1:
            raise ValueError("page is 1-based")
        return page


class ExtractedLine(_Strict):
    description: ExtractedField
    sku: ExtractedField
    quantity: ExtractedField
    unit_price: ExtractedField
    amount: ExtractedField
    tax_rate: ExtractedField


class InvoiceExtraction(_Strict):
    supplier_name: ExtractedField
    supplier_tax_id: ExtractedField
    supplier_address: ExtractedField
    supplier_bank_account: ExtractedField
    invoice_number: ExtractedField
    invoice_date: ExtractedField
    due_date: ExtractedField
    po_number: ExtractedField
    currency: ExtractedField
    subtotal: ExtractedField
    tax_total: ExtractedField
    total: ExtractedField
    payment_terms: ExtractedField
    lines: list[ExtractedLine]


def tool_input_schema() -> dict[str, Any]:
    """JSON schema for the forced/strict extraction tool."""
    return InvoiceExtraction.model_json_schema()


def pages_out_of_range(extraction: InvoiceExtraction, page_count: int) -> list[str]:
    """Names of fields whose page number is beyond the document (the model made it up)."""
    bad: list[str] = []
    for name in HEADER_FIELDS:
        page = getattr(extraction, name).page
        if page is not None and page > page_count:
            bad.append(name)
    for n, line in enumerate(extraction.lines, start=1):
        for name in LINE_FIELDS:
            page = getattr(line, name).page
            if page is not None and page > page_count:
                bad.append(f"lines[{n}].{name}")
    return bad
