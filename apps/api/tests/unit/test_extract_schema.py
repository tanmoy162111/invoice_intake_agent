from typing import Any

import pytest
from pydantic import ValidationError

from intake.core.confidence import SelfConfidence
from intake.extract.schema import (
    HEADER_FIELDS,
    LINE_FIELDS,
    InvoiceExtraction,
    tool_input_schema,
)

# Keywords the strict-tool grammar does not support (platform.claude.com structured outputs).
UNSUPPORTED = {"minimum", "maximum", "multipleOf", "minLength", "maxLength", "pattern"}


def field(value: str | None, conf: str = "high", page: int | None = 1) -> dict[str, Any]:
    return {"value": value, "self_confidence": conf, "page": page}


def payload(**overrides: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {name: field("x") for name in HEADER_FIELDS}
    data["lines"] = [{name: field("1") for name in LINE_FIELDS}]
    data.update(overrides)
    return data


def walk(node: Any) -> Any:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def test_header_fields_match_playbook() -> None:
    assert HEADER_FIELDS == (
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
    assert LINE_FIELDS == ("description", "sku", "quantity", "unit_price", "amount", "tax_rate")


def test_every_object_in_schema_forbids_extra_properties() -> None:
    objects = [n for n in walk(tool_input_schema()) if n.get("type") == "object"]
    assert objects
    assert all(n.get("additionalProperties") is False for n in objects)


def test_schema_uses_no_unsupported_keywords() -> None:
    for node in walk(tool_input_schema()):
        assert not UNSUPPORTED & node.keys(), node


def test_schema_has_no_type_arrays() -> None:
    # ["string", "null"] is unsupported by strict tools; nullable must use anyOf.
    for node in walk(tool_input_schema()):
        assert not isinstance(node.get("type"), list), node


def test_every_header_field_and_lines_are_required() -> None:
    schema = tool_input_schema()
    assert set(schema["required"]) == {*HEADER_FIELDS, "lines"}


def test_valid_payload_parses() -> None:
    parsed = InvoiceExtraction.model_validate(payload())
    assert parsed.po_number.value == "x"
    assert parsed.lines[0].amount.self_confidence is SelfConfidence.HIGH


def test_missing_po_number_is_null_not_invented() -> None:
    parsed = InvoiceExtraction.model_validate(payload(po_number=field(None, "low", None)))
    assert parsed.po_number.value is None
    assert parsed.po_number.page is None


def test_extra_property_rejected() -> None:
    with pytest.raises(ValidationError):
        InvoiceExtraction.model_validate(payload(surprise=field("x")))


def test_missing_field_rejected() -> None:
    data = payload()
    del data["total"]
    with pytest.raises(ValidationError):
        InvoiceExtraction.model_validate(data)


def test_bad_confidence_rejected() -> None:
    with pytest.raises(ValidationError):
        InvoiceExtraction.model_validate(payload(total=field("1", "certain")))


@pytest.mark.parametrize("page", [0, -1])
def test_page_is_one_based(page: int) -> None:
    with pytest.raises(ValidationError):
        InvoiceExtraction.model_validate(payload(total=field("1", page=page)))


def test_value_must_be_a_string_or_null() -> None:
    with pytest.raises(ValidationError):
        InvoiceExtraction.model_validate(payload(total=field(12.5)))  # type: ignore[arg-type]


def test_no_lines_is_allowed() -> None:
    assert InvoiceExtraction.model_validate(payload(lines=[])).lines == []
