"""The Australian and New Zealand golden supplement: eight invoices in AUD and NZD.

    make generate-au-nz

Why it exists: the main golden set is USD, EUR and GBP only, so it cannot show whether AUD and NZD
are read correctly. These eight invoices print amounts the ways Australian and New Zealand suppliers
do (a bare `$`, `A$` / `NZ$`, or a code), in clean, scanned and photographed form, with GST at 10%
(Australia) and 15% (New Zealand).

Everything is synthetic and fixed in this file (no random content). Supplier names are invented. The
tax numbers are generated with the real check-digit rules so they look right to a validator, and may
by chance match a real business: they are test data and must never be used as anything else.

Files go to `data/golden/au_nz/` and are create-only: an existing file is never overwritten, like the
main golden set. The main set under `data/golden/` and `data/seed/` is not touched.
"""

import random
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import config
from .generate import GOLDEN_DIR, master_data, render_invoice, truth_for, write_json
from .model import InvLine, InvoiceSpec, PoLine, PurchaseOrder, Receipt, Supplier, World
from .world import tax_of

OUT = GOLDEN_DIR / "au_nz"
SEED = 20261027
GST_BP = {"AUD": 1000, "NZD": 1500}


# ----------------------------------------------------------------------------- tax numbers


def abn(rng: random.Random) -> str:
    """An 11-digit Australian Business Number with a valid check (weights 10,1,3,5,...,19; the
    first digit has 1 taken off; the weighted sum must be a multiple of 89)."""
    weights = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    while True:
        digits = [rng.randrange(1, 10)] + [rng.randrange(10) for _ in range(9)]
        # choose the last digit so that the sum is a multiple of 89
        base = sum(w * d for w, d in zip(weights[:10], [digits[0] - 1, *digits[1:]], strict=True))
        for last in range(10):
            if (base + weights[10] * last) % 89 == 0:
                d = [*digits, last]
                s = "".join(map(str, d))
                return f"{s[:2]} {s[2:5]} {s[5:8]} {s[8:]}"


def ird(rng: random.Random) -> str:
    """A New Zealand IRD / GST number (8 digits plus check digit, modulus 11), formatted 123-456-789."""
    primary, secondary = [3, 2, 7, 6, 5, 4, 3, 2], [7, 4, 3, 2, 5, 2, 7, 6]
    while True:
        body = [rng.randrange(10) for _ in range(8)]
        if not 10_000_000 <= int("".join(map(str, body))) <= 150_000_000:
            continue
        check = 11 - sum(w * d for w, d in zip(primary, body, strict=True)) % 11
        if check == 11:
            check = 0
        if check == 10:
            check = 11 - sum(w * d for w, d in zip(secondary, body, strict=True)) % 11
            if check in (10, 11):
                continue
        s = "".join(map(str, [*body, check]))
        return f"{s[:3]}-{s[3:6]}-{s[6:]}"


def valid_abn(text: str) -> bool:
    digits = [int(c) for c in text if c.isdigit()]
    if len(digits) != 11:
        return False
    digits[0] -= 1
    weights = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    return sum(w * d for w, d in zip(weights, digits, strict=True)) % 89 == 0


def valid_ird(text: str) -> bool:
    digits = [int(c) for c in text if c.isdigit()]
    if len(digits) not in (8, 9):
        return False
    digits = [0] * (9 - len(digits)) + digits
    body, check = digits[:8], digits[8]
    for weights in ([3, 2, 7, 6, 5, 4, 3, 2], [7, 4, 3, 2, 5, 2, 7, 6]):
        c = 11 - sum(w * d for w, d in zip(weights, body, strict=True)) % 11
        c = 0 if c == 11 else c
        if c < 10:
            return c == check
    return False


# ----------------------------------------------------------------------------- the fixed content

# key, name, currency, category, layout, prefix, aliases, address, bank, tax-id label
SUPPLIERS: list[tuple[str, str, str, str, str, str, list[str], str, str, str]] = [
    ("wattle", "Wattle & Co Office Supplies Pty Ltd", "AUD", "office", "A", "WC",
     ["Wattle Office"], "14 Bridge Street, Sydney NSW 2000", "BSB 062-000 / Acct 4471 2290", "ABN"),
    ("harbourline", "Harbourline Packaging Pty Ltd", "AUD", "packaging", "B", "HP",
     ["Harbourline"], "220 Collins Street, Melbourne VIC 3000", "BSB 033-120 / Acct 8812 6403", "ABN"),
    ("ironbark", "Ironbark Industrial Supplies Pty Ltd", "AUD", "industrial", "A", "IB",
     ["Ironbark Industrial"], "9 Wharf Road, Brisbane QLD 4000", "BSB 124-001 / Acct 3305 7716", "ABN"),
    ("kowhai", "Kowhai Stationery Limited", "NZD", "office", "B", "KS",
     ["Kowhai"], "52 Queen Street, Auckland 1010", "12-3042-0198557-00", "GST No."),
    ("pohutukawa", "Pohutukawa Cleaning Services Limited", "NZD", "facility", "D", "PC",
     ["Pohutukawa Cleaning"], "31 Lambton Quay, Wellington 6011", "03-1502-0771342-00", "GST No."),
    ("tui", "Tui Hardware Supplies Limited", "NZD", "industrial", "A", "TH",
     ["Tui Hardware"], "7 Moorhouse Avenue, Christchurch 8011", "06-0818-0245119-00", "GST No."),
]  # fmt: skip

# supplier, number, date, quality, money style, [(sku, description, qty, unit price in cents)]
INVOICES: list[tuple[str, str, str, str, str, list[tuple[str, str, int, int]]]] = [
    ("wattle", "WC-2026-0101", "2026-09-07", "clean", "symbol",
     [("WC-A4-5", "A4 copy paper, 5 ream carton", 12, 4950),
      ("WC-PEN-B", "Ballpoint pens, box of 50", 6, 1895),
      ("WC-FLD-L", "Lever arch folders", 24, 640)]),
    ("harbourline", "HP-26-3318", "2026-09-14", "clean", "prefixed",
     [("HP-BX-M", "Corrugated carton, medium", 400, 285),
      ("HP-TP-48", "Packing tape 48mm, 36 pack", 10, 7260)]),
    ("ironbark", "IB-5520", "2026-09-21", "clean", "code",
     [("IB-M10-50", "M10 x 50 bolts, box of 100", 15, 3880),
      ("IB-WSH-10", "M10 washers, box of 500", 8, 2475),
      ("IB-GLV-L", "Cut-resistant gloves, large", 40, 1290)]),
    ("wattle", "WC-2026-0124", "2026-10-02", "scanned", "symbol",
     [("WC-TNR-12", "Toner cartridge, black", 4, 18950),
      ("WC-A4-5", "A4 copy paper, 5 ream carton", 6, 4950)]),
    ("kowhai", "KS-70412", "2026-09-10", "clean", "symbol",
     [("KS-EXB-80", "Exercise books, 80 page, pack of 20", 30, 2490),
      ("KS-MKR-W", "Whiteboard markers, box of 12", 15, 1850),
      ("KS-STP-26", "Staples 26/6, 20 boxes", 5, 1295)]),
    ("pohutukawa", "PC/2026/0881", "2026-09-28", "clean", "prefixed",
     [("PC-WKL", "Weekly office clean, per visit", 4, 21500),
      ("PC-CAR", "Carpet steam clean, per room", 3, 14500)]),
    ("tui", "TH-33907", "2026-10-05", "clean", "code",
     [("TH-DRL-18", "Cordless drill 18V", 3, 23900),
      ("TH-SCR-100", "Decking screws 100 x 4.5, 1kg", 20, 2190),
      ("TH-LDR-24", "Aluminium step ladder 2.4m", 2, 18900)]),
    ("kowhai", "KS-70977", "2026-10-12", "photo", "prefixed",
     [("KS-A3-PD", "A3 drawing pads, pack of 10", 8, 6890),
      ("KS-EXB-80", "Exercise books, 80 page, pack of 20", 10, 2490)]),
]  # fmt: skip


def build() -> World:
    rng = random.Random(SEED)
    suppliers = [
        Supplier(
            key=k, name=name, tax_id=abn(rng) if cur == "AUD" else ird(rng), address=addr,
            bank_account=bank, currency=cur, tax_rate_bp=GST_BP[cur], category=cat, layout=layout,
            prefix=prefix, aliases=aliases,
        )
        for k, name, cur, cat, layout, prefix, aliases, addr, bank, _ in SUPPLIERS
    ]  # fmt: skip
    by_key = {s.key: s for s in suppliers}
    label = {row[0]: row[9] for row in SUPPLIERS}
    pos: list[PurchaseOrder] = []
    invoices: list[InvoiceSpec] = []
    for n, (key, number, day, quality, style, items) in enumerate(INVOICES):
        sup = by_key[key]
        d = date.fromisoformat(day)
        po_number = f"PO-AU{900 + n}" if sup.currency == "AUD" else f"PO-NZ{900 + n}"
        po_lines = [
            PoLine(i + 1, sku, desc, qty, price) for i, (sku, desc, qty, price) in enumerate(items)
        ]
        pos.append(
            PurchaseOrder(
                po_number=po_number,
                supplier_key=key,
                currency=sup.currency,
                lines=po_lines,
                receipts=[
                    Receipt(
                        received_at=(d - timedelta(days=3)).isoformat(),
                        qty_by_line={ln.line_no: ln.qty for ln in po_lines},
                    )
                ],
            )
        )
        lines = [
            InvLine(ln.line_no, ln.sku, ln.description, ln.qty, ln.unit_price_minor,
                    ln.qty * ln.unit_price_minor)
            for ln in po_lines
        ]  # fmt: skip
        subtotal = sum(ln.amount_minor for ln in lines)
        tax = tax_of(subtotal, sup.tax_rate_bp)
        invoices.append(
            InvoiceSpec(
                id=f"inv-{901 + n}", supplier_key=key, supplier_name=sup.name,
                supplier_tax_id=sup.tax_id, supplier_address=sup.address,
                bank_account=sup.bank_account, layout=sup.layout, invoice_number=number,
                invoice_date=day, due_date=(d + timedelta(days=30)).isoformat(),
                po_number=po_number, currency=sup.currency, tax_rate_bp=sup.tax_rate_bp,
                payment_terms="Net 30", lines=lines, subtotal_minor=subtotal, tax_minor=tax,
                total_minor=subtotal + tax, quality=quality, received_order=901 + n,
                money_style=style, tax_id_label=label[key], tax_label="GST",
            )
        )  # fmt: skip
    return World(suppliers, pos, invoices)


def file_name(inv: InvoiceSpec) -> str:
    if inv.quality == "clean":
        slug = inv.supplier_name.split()[0].replace("&", "").strip()
        safe = inv.invoice_number.replace("/", "-")
        return f"{slug}-{safe}.pdf"
    if inv.quality == "photo":
        return f"IMG_{4000 + inv.received_order * 7:04d}.jpg"
    return f"scan{inv.received_order:04d}.pdf"


def supplement_master(world: World) -> dict[str, Any]:
    """Master data for these suppliers. The tenant block is the seed's, plus AUD and NZD approval
    limits: with no limit for its currency an invoice always goes to review."""
    data = master_data(world)
    limits = dict(config.TENANT_SETTINGS["approval_amount_limits_minor"])  # type: ignore[call-overload]
    limits.update({"AUD": 1_000_000, "NZD": 1_000_000})
    data["tenant"]["settings"] = {**config.TENANT_SETTINGS, "approval_amount_limits_minor": limits}
    return data


README = """# Australian and New Zealand golden supplement

Eight synthetic invoices (4 AUD, 4 NZD) that exercise the currencies the main golden set cannot:
bare `$`, `A$` / `NZ$` and code (`AUD 1,234.50`) amounts, GST at 10% and 15%, clean, scanned and
photographed. They are separate from the 60-invoice golden set in `../` on purpose, so that set,
its tests and published numbers do not change.

- `invoices/` and `truth/`: the documents and their answer keys (same format as `../truth`).
- `master.json`: the suppliers, purchase orders and receipts they refer to, plus AUD and NZD
  approval limits. Load it **after** `data/seed/master.json`; without it the suppliers are unknown.
- Everything is synthetic. Tax numbers pass the real check-digit rules but are test data.
- Regenerate with `make generate-au-nz`. Existing files are never overwritten.
- `make eval` does not read this folder yet. When the evaluation harness (M10) is merged it has to be
  pointed at `au_nz/truth` and `au_nz/master.json`; until then these files are inert.
"""


def main() -> None:
    world = build()
    for sub in ("invoices", "truth"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        for inv in world.invoices:
            fname = file_name(inv)
            target = OUT / "invoices" / fname
            truth_path = OUT / "truth" / f"{inv.id}.json"
            if target.exists() and truth_path.exists():
                continue  # create-only
            render_invoice(inv, target, Path(tmpdir))
            truth = truth_for(inv, world, fname, "golden")
            truth["supplement"] = "au_nz"
            write_json(truth_path, truth)
    for name, text in (("master.json", None), ("README.md", README)):
        path = OUT / name
        if path.exists():
            continue
        if text is None:
            write_json(path, supplement_master(world))
        else:
            path.write_text(text)
    print(f"wrote {len(world.invoices)} AU/NZ invoices to {OUT}")


if __name__ == "__main__":
    main()
