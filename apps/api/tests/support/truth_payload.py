"""The answer a perfect reader would give for a seed invoice, printed the way its layout prints it.

Built from the generator's ground truth, so it is synthetic, not a real model answer. It exercises
the whole pipeline (real PDFs, real text layers, per-layout date and number formats) without the
network. Real recorded answers replace it once a model run is approved.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from intake.seed import DEFAULT_SEED_DIR

DATE_FORMATS = {
    "A": "%Y-%m-%d",
    "B": "%d %b %Y",
    "C": "%B %d, %Y",
    "D": "%d.%m.%Y",
    "E": "%d-%b-%Y",
}
SKU_LAYOUTS = {"C", "D"}  # only these layouts print a SKU column
RATE_LAYOUTS = {"D"}  # only this layout prints a tax % per line
CURRENCY_TEXT = {"USD": "$", "GBP": "£", "EUR": "EUR"}


def load_truths(quality: str = "clean") -> list[dict[str, Any]]:
    paths = sorted((DEFAULT_SEED_DIR / "truth").glob("*.json"))
    truths = [json.loads(p.read_text()) for p in paths]
    return [t for t in truths if t["doc_quality"] == quality and t["readable"]]


def seed_file(truth: dict[str, Any]) -> Path:
    return DEFAULT_SEED_DIR / "invoices" / truth["file"]


def money_text(minor: int, currency: str) -> str:
    whole, frac = divmod(abs(minor), 100)
    sign = "-" if minor < 0 else ""
    if currency == "EUR":
        return f"{sign}{whole:,}".replace(",", ".") + f",{frac:02d} EUR"
    return f"{sign}{CURRENCY_TEXT[currency]}{whole:,}.{frac:02d}"


def _f(value: str | None, page: int = 1) -> dict[str, Any]:
    return {"value": value, "self_confidence": "high", "page": page if value is not None else None}


def payload_from_truth(truth: dict[str, Any]) -> dict[str, Any]:
    h, layout, cur = truth["header"], truth["layout"], truth["header"]["currency"]

    def day(iso: str | None) -> str | None:
        return date.fromisoformat(iso).strftime(DATE_FORMATS[layout]) if iso else None

    def money(minor: int | None) -> str | None:
        return None if minor is None else money_text(minor, cur)

    out: dict[str, Any] = {
        "supplier_name": _f(h["supplier_name"]),
        "supplier_tax_id": _f(h["supplier_tax_id"]),
        "supplier_address": _f(h["supplier_address"]),
        "supplier_bank_account": _f(h["supplier_bank_account"]),
        "invoice_number": _f(h["invoice_number"]),
        "invoice_date": _f(day(h["invoice_date"])),
        "due_date": _f(day(h["due_date"])),
        "po_number": _f(h["po_number"]),
        "currency": _f(CURRENCY_TEXT[cur]),
        "subtotal": _f(money(h["subtotal_minor"])),
        "tax_total": _f(money(h["tax_total_minor"])),
        "total": _f(money(h["total_minor"])),
        "payment_terms": _f(h["payment_terms"]),
        "lines": [],
    }
    for ln in truth["lines"]:
        rate = ln.get("tax_rate_bp")
        out["lines"].append(
            {
                "description": _f(ln["description"]),
                "sku": _f(ln["sku"] if layout in SKU_LAYOUTS else None),
                "quantity": _f(format(Decimal(str(ln["quantity"])).normalize(), "f")),
                "unit_price": _f(money(ln["unit_price_minor"])),
                "amount": _f(money(ln["amount_minor"])),
                "tax_rate": _f(
                    f"{Decimal(rate) / 100:g}%"
                    if rate is not None and layout in RATE_LAYOUTS
                    else None
                ),
            }
        )
    return out
