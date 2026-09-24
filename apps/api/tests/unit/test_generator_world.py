"""Checks on the synthetic world (no rendering, no I/O)."""

from collections import Counter
from functools import cache

import pytest
from generator import config
from generator.model import InvoiceSpec, World
from generator.world import PLAN, TOTAL_INVOICES, build_world, tax_of, transpose

from intake.core.exceptions import ExceptionCode


@cache
def world() -> World:
    return build_world()


def by_code(code: str) -> list[InvoiceSpec]:
    return [i for i in world().invoices if code in i.must_raise]


def test_is_deterministic() -> None:
    assert build_world() == build_world()
    assert build_world(seed=1) != build_world()


def test_size_and_suppliers() -> None:
    w = world()
    assert len(w.invoices) == TOTAL_INVOICES
    assert len(w.suppliers) == 15
    assert {s.layout for s in w.suppliers} == set("ABCDE")


def test_every_exception_code_is_planted_at_least_once() -> None:
    planted = {c for i in world().invoices for c in i.must_raise}
    assert planted == {c.value for c in ExceptionCode}


@pytest.mark.parametrize("code", sorted(PLAN))
def test_plan_counts(code: str) -> None:
    expected = PLAN[code] + (0 if code != "PO_OVERBILLED" else 0)
    assert len(by_code(code)) == expected


def test_unreadable_documents_are_exactly_two() -> None:
    assert len(by_code("UNREADABLE_DOCUMENT")) == 2


def test_exactly_one_changed_bank_account_and_it_differs_from_file() -> None:
    (inv,) = by_code("BANK_DETAILS_CHANGED")
    supplier = next(s for s in world().suppliers if s.key == inv.supplier_key)
    assert inv.bank_account != supplier.bank_account


def test_ids_and_received_order_are_unique_and_dependencies_come_first() -> None:
    invs = world().invoices
    assert len({i.id for i in invs}) == len(invs)
    assert [i.received_order for i in invs] == list(range(1, len(invs) + 1))
    order = {i.id: i.received_order for i in invs}
    for i in invs:
        if i.duplicate_of:
            assert order[i.duplicate_of] < order[i.id]


def test_quality_mix_is_roughly_half_clean_30_scanned_20_photo() -> None:
    counts = Counter(i.quality for i in world().invoices)
    n = len(world().invoices)
    assert counts["unknown"] == 1
    assert abs(counts["clean"] - 0.5 * n) <= 5
    assert abs(counts["scanned"] - 0.3 * n) <= 5
    assert abs(counts["photo"] - 0.2 * n) <= 5


def test_smudged_documents_are_image_documents() -> None:
    for i in world().invoices:
        if i.obscure:
            assert i.quality in {"scanned", "photo"}


def test_unplanted_invoice_arithmetic_is_consistent() -> None:
    for i in world().invoices:
        if i.planted:
            continue
        assert all(ln.amount_minor == ln.qty * ln.unit_price_minor for ln in i.lines)
        assert i.subtotal_minor == sum(ln.amount_minor for ln in i.lines)
        assert i.tax_minor == tax_of(i.subtotal_minor, i.tax_rate_bp)
        assert i.total_minor == i.subtotal_minor + i.tax_minor


def test_clean_invoices_match_their_po_and_receipts() -> None:
    w = world()
    pos = {p.po_number: p for p in w.purchase_orders}
    overbilled_pos = {i.po_number for i in by_code("PO_OVERBILLED")}
    checked = 0
    for i in w.invoices:
        if i.planted or i.duplicate_of or i.po_number in overbilled_pos:
            continue
        po = pos[i.po_number or ""]
        assert po.currency == i.currency
        assert po.total_minor == i.subtotal_minor
        for ln in i.lines:
            po_line = next(x for x in po.lines if x.line_no == ln.line_no)
            assert (ln.qty, ln.unit_price_minor) == (po_line.qty, po_line.unit_price_minor)
            assert sum(r.qty_by_line.get(ln.line_no, 0) for r in po.receipts) == ln.qty
        checked += 1
    assert checked >= 40


def test_only_planted_above_limit_invoices_exceed_the_approval_limit() -> None:
    limit = config.TENANT_SETTINGS["approval_amount_limit_minor"]
    for i in world().invoices:
        if i.currency == "USD" and i.total_minor > limit:
            assert "ABOVE_APPROVAL_LIMIT" in i.must_raise, i.id


def test_no_po_invoices_have_no_matching_po_for_the_supplier() -> None:
    w = world()
    for i in by_code("NO_PO"):
        assert i.po_number is None
        assert all(
            p.total_minor != i.subtotal_minor
            for p in w.purchase_orders
            if p.supplier_key == i.supplier_key
        )


def test_po_not_found_cites_a_missing_po() -> None:
    known = {p.po_number for p in world().purchase_orders}
    for i in by_code("PO_NOT_FOUND"):
        assert i.po_number and i.po_number not in known


def test_overbilled_po_total_is_exceeded_only_by_the_second_invoice() -> None:
    w = world()
    (second,) = by_code("PO_OVERBILLED")
    po = next(p for p in w.purchase_orders if p.po_number == second.po_number)
    peers = [i for i in w.invoices if i.po_number == po.po_number and not i.duplicate_of]
    assert len(peers) == 2
    assert sum(i.subtotal_minor for i in peers) > po.total_minor
    first = next(i for i in peers if i is not second)
    assert first.subtotal_minor <= po.total_minor and not first.planted


def test_transpose_changes_the_number() -> None:
    assert transpose(214000) == 124000
    assert transpose(1111) == 2011  # falls back to +900
