"""Builds the synthetic world: suppliers, POs, receipts, and invoices with planted problems.

Pure and deterministic: no file I/O, no rendering. `build_world(seed)` always returns the same
world for the same seed. Money is integer minor units.

Conventions the truth data follows (M4-M7 must implement the same):
- PO total is net of tax (sum of PO line amounts); it is compared with the invoice subtotal.
- The approval limit applies to the invoice total including tax.
- `must_raise` are the planted problems; `may_raise` are knock-on codes that are acceptable but
  not required; "*" means any code is acceptable (the document is unreadable).
"""

import random
from dataclasses import replace
from datetime import date, timedelta

from . import config
from .model import (
    InvLine,
    InvoiceSpec,
    Planted,
    PoLine,
    PurchaseOrder,
    Receipt,
    Supplier,
    World,
)

# ----------------------------------------------------------------------------- catalogs

# (sku suffix, description, unit price in minor units)
CATALOGS: dict[str, list[tuple[str, str, int]]] = {
    "office": [
        ("A4-500", "A4 copier paper, 500 sheets", 645),
        ("PEN-BX", "Ballpoint pens, box of 50", 1290),
        ("TON-BK", "Toner cartridge, black", 8950),
        ("FLD-100", "Manila folders, pack of 100", 1875),
        ("STP-HD", "Heavy-duty stapler", 2490),
        ("NOT-12", "Notebooks, pack of 12", 2240),
        ("CHR-ERG", "Ergonomic desk chair", 18900),
        ("LBL-RL", "Label roll, 1000 labels", 1150),
    ],
    "packaging": [
        ("BOX-M", "Corrugated box, medium", 185),
        ("BOX-L", "Corrugated box, large", 265),
        ("TAPE-48", "Packing tape 48mm, roll", 349),
        ("BUB-50", "Bubble wrap, 50m roll", 2150),
        ("PAL-STD", "Wooden pallet, standard", 1450),
        ("STR-WR", "Stretch wrap, 500mm roll", 1275),
        ("LBL-SHP", "Shipping labels, box of 500", 1890),
        ("MAIL-B", "Padded mailers, pack of 100", 3120),
    ],
    "industrial": [
        ("BRG-6204", "Ball bearing 6204", 1240),
        ("BLT-M8", "Hex bolt M8x40, box of 200", 2890),
        ("GSK-SET", "Gasket set, universal", 4550),
        ("HYD-HS", "Hydraulic hose, 2m", 7800),
        ("FIL-OIL", "Oil filter, heavy duty", 2360),
        ("VLV-BL", "Ball valve 1 inch", 5490),
        ("MTR-1HP", "Electric motor 1HP", 48500),
        ("LUB-5L", "Industrial lubricant, 5L", 6320),
    ],
    "facility": [
        ("CLN-MNT", "Monthly cleaning service, floor 1", 42000),
        ("WIN-QTR", "Window cleaning, per visit", 18500),
        ("HVAC-SV", "HVAC service call", 27500),
        ("PST-CTL", "Pest control treatment", 9800),
        ("WST-PK", "Waste collection, per pickup", 6500),
        ("LGT-RPL", "Light fitting replacement", 4400),
        ("SNT-KIT", "Sanitiser refill kit", 3250),
        ("CRP-CLN", "Carpet cleaning, per room", 8900),
    ],
    "it": [
        ("LAP-14", "Laptop 14 inch, 16GB", 112000),
        ("MON-27", "Monitor 27 inch", 28900),
        ("KB-WL", "Wireless keyboard", 4990),
        ("MS-WL", "Wireless mouse", 2790),
        ("DOC-USBC", "USB-C docking station", 15900),
        ("SSD-1T", "SSD 1TB", 8990),
        ("SW-LIC", "Software licence, annual", 24000),
        ("NET-SW8", "Network switch 8-port", 12900),
    ],
    "lab": [
        ("PIP-TIP", "Pipette tips, rack of 96", 890),
        ("GLV-NIT", "Nitrile gloves, box of 100", 1290),
        ("BEA-250", "Glass beaker 250ml", 640),
        ("REA-ETH", "Ethanol 99%, 2.5L", 3890),
        ("CEN-TUB", "Centrifuge tubes, pack of 500", 5250),
        ("MIC-SLD", "Microscope slides, box of 72", 1490),
        ("BAL-DIG", "Digital balance 0.01g", 42500),
        ("SAF-GOG", "Safety goggles", 1180),
    ],
    "catering": [
        ("COF-1KG", "Ground coffee 1kg", 1890),
        ("TEA-100", "Tea bags, box of 100", 640),
        ("WAT-24", "Bottled water, case of 24", 1120),
        ("CUP-1K", "Paper cups, sleeve of 1000", 4350),
        ("SNK-BX", "Snack box assortment", 3890),
        ("MLK-12", "UHT milk, case of 12", 1440),
        ("SUG-5K", "Sugar 5kg", 860),
        ("NAP-2K", "Paper napkins, pack of 2000", 2190),
    ],
}

SUPPLIER_DEFS = [
    # key, name, currency, tax bp, category, layout, prefix, aliases
    ("brightline", "Brightline Office Supplies Inc.", "USD", 800, "office", "A", "BL", ["Brightline Office"]),
    ("coastal", "Coastal Packaging Ltd.", "USD", 700, "packaging", "B", "CP", ["Coastal Pack"]),
    ("meridian", "Meridian Industrial Parts LLC", "USD", 600, "industrial", "C", "MIP", ["Meridian Parts"]),
    ("harborview", "Harborview Cleaning Services", "USD", 500, "facility", "D", "HV", ["Harborview"]),
    ("summit", "Summit IT Hardware Corp.", "USD", 1000, "it", "E", "SIT", ["Summit IT"]),
    ("keystone", "Keystone Printing Co.", "USD", 800, "office", "B", "KP", ["Keystone Print"]),
    ("redwood", "Redwood Facility Maintenance", "USD", 700, "facility", "A", "RFM", ["Redwood FM"]),
    ("pioneer", "Pioneer Lab Equipment Inc.", "USD", 900, "lab", "C", "PLE", ["Pioneer Lab"]),
    ("apex", "Apex Fastener Supply Co.", "USD", 750, "industrial", "E", "AFS", ["Apex Fasteners"]),
    ("rheinwerk", "Rheinwerk Bürobedarf GmbH", "EUR", 1900, "office", "D", "RW", ["Rheinwerk"]),
    ("lumiere", "Lumière Fournitures SARL", "EUR", 2000, "packaging", "A", "LF", ["Lumiere Fournitures"]),
    ("olivar", "Olivar Suministros S.L.", "EUR", 2100, "catering", "C", "OS", ["Olivar"]),
    ("thames", "Thames Valley Stationers Ltd", "GBP", 2000, "office", "B", "TVS", ["Thames Valley"]),
    ("pennine", "Pennine Engineering Supplies Ltd", "GBP", 2000, "industrial", "E", "PES", ["Pennine Eng"]),
    ("caledonian", "Caledonian Catering Supplies Ltd", "GBP", 500, "catering", "D", "CCS", ["Caledonian"]),
]  # fmt: skip

STREETS = ["Market St", "Harbour Rd", "Industrial Way", "Mill Lane", "Station Rd", "Oak Ave"]
CITIES = {
    "USD": ["Portland, OR", "Austin, TX", "Columbus, OH", "Tampa, FL"],
    "EUR": ["Köln", "Lyon", "Valencia", "Utrecht"],
    "GBP": ["Leeds", "Glasgow", "Bristol", "Sheffield"],
}
FX = {("USD", "EUR"): (92, 100), ("USD", "GBP"): (79, 100), ("EUR", "USD"): (109, 100)}

UNKNOWN_SUPPLIERS = [
    ("Brightline Offices Supplies Ltd", "office", "USD"),  # near-miss of a real supplier
    ("Coastal Packing Ltd.", "packaging", "USD"),  # near-miss of a real supplier
    ("Zenith Novelty Traders", "catering", "USD"),  # unrelated
]

# How many invoices to plant per problem, and the total size of the set.
PLAN = {
    "UNREADABLE_DOCUMENT": 2,
    "LOW_CONFIDENCE_FIELD": 4,
    "LINE_MATH_MISMATCH": 4,
    "TOTAL_MISMATCH": 4,
    "TAX_MISMATCH": 3,
    "INVALID_DATE": 3,
    "UNKNOWN_SUPPLIER": 3,
    "BANK_DETAILS_CHANGED": 1,
    "POSSIBLE_DUPLICATE": 5,
    "NO_PO": 4,
    "PO_NOT_FOUND": 3,
    "PRICE_VARIANCE": 5,
    "QTY_VARIANCE": 4,
    "RECEIPT_MISSING": 4,
    "QTY_NOT_RECEIVED": 4,
    "PO_OVERBILLED": 1,  # one pair of invoices; the second one is over-billed
    "CURRENCY_MISMATCH": 3,
    "ABOVE_APPROVAL_LIMIT": 3,
}
TOTAL_INVOICES = 120
INVOICE_CAP_MINOR = 800_000  # keeps ordinary invoices (with tax) under the approval limit


# ----------------------------------------------------------------------------- helpers


def tax_of(subtotal: int, bp: int) -> int:
    """Tax in minor units, rounded half up."""
    return (subtotal * bp + 5000) // 10000


def transpose(n: int) -> int:
    """Swap the first two adjacent differing digits (2140 -> 2410), a classic typo."""
    s = str(n)
    for i in range(len(s) - 1):
        if s[i] != s[i + 1]:
            swapped = s[:i] + s[i + 1] + s[i] + s[i + 2 :]
            if not swapped.startswith("0"):
                return int(swapped)
    return n + 900


def _digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randrange(10)) for _ in range(n))


def _bank_account(rng: random.Random, currency: str) -> str:
    if currency == "USD":
        return f"ABA {_digits(rng, 9)} / Acct {_digits(rng, 10)}"
    cc = "DE" if currency == "EUR" else "GB"
    body = _digits(rng, 20)
    return f"{cc}{_digits(rng, 2)} " + " ".join(body[i : i + 4] for i in range(0, 20, 4))


def make_suppliers(rng: random.Random) -> list[Supplier]:
    out = []
    for key, name, cur, bp, cat, layout, prefix, aliases in SUPPLIER_DEFS:
        addr = f"{rng.randrange(10, 990)} {rng.choice(STREETS)}, {rng.choice(CITIES[cur])}"
        tax_id = {"USD": f"{_digits(rng, 2)}-{_digits(rng, 7)}", "EUR": f"DE{_digits(rng, 9)}"}.get(
            cur, f"GB{_digits(rng, 9)}"
        )
        out.append(
            Supplier(
                key=key,
                name=name,
                tax_id=tax_id,
                address=addr,
                bank_account=_bank_account(rng, cur),
                currency=cur,
                tax_rate_bp=bp,
                category=cat,
                layout=layout,
                prefix=prefix,
                aliases=aliases,
            )
        )
    return out


# ----------------------------------------------------------------------------- builder


class Builder:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.suppliers = make_suppliers(self.rng)
        self.by_key = {s.key: s for s in self.suppliers}
        self.pos: list[PurchaseOrder] = []
        self.invoices: list[InvoiceSpec] = []
        self._po_seq = 10000
        self._inv_seq: dict[str, int] = {
            s.key: self.rng.randrange(100, 900) for s in self.suppliers
        }
        self._id_seq = 0
        self.after: dict[str, str] = {}  # invoice id -> id that must be received earlier

    # -- small utilities
    def usd_suppliers(self) -> list[Supplier]:
        return [s for s in self.suppliers if s.currency == "USD"]

    def pick_supplier(self, currency: str | None = None) -> Supplier:
        pool = [s for s in self.suppliers if currency in (None, s.currency)]
        return self.rng.choice(pool)

    def next_po_number(self) -> str:
        self._po_seq += 1
        return f"PO-{self._po_seq}"

    def next_invoice_number(self, sup: Supplier) -> str:
        self._inv_seq[sup.key] += self.rng.randrange(1, 9)
        return f"{sup.prefix}-2026-{self._inv_seq[sup.key]:04d}"

    def next_id(self) -> str:
        self._id_seq += 1
        return f"inv-{self._id_seq:03d}"

    def pick_date(self) -> date:
        return date(2026, self.rng.randrange(3, 9), self.rng.randrange(13, 29))

    # -- construction
    def po_lines_for(self, sup: Supplier, cap: int, floor: int = 0) -> list[PoLine]:
        catalog = CATALOGS[sup.category]
        for _ in range(200):
            k = self.rng.randrange(1, 5)
            items = self.rng.sample(catalog, k)
            lines = []
            for n, (sku, desc, price) in enumerate(items, start=1):
                qty = self.rng.choice([1, 2, 3, 5, 10, 12, 20, 24, 50])
                lines.append(PoLine(n, f"{sup.prefix}-{sku}", desc, qty, price))
            total = sum(ln.qty * ln.unit_price_minor for ln in lines)
            if floor <= total <= cap:
                return lines
            # scale quantities down (or up) until the total fits
            if total > cap:
                for ln in lines:
                    ln.qty = max(1, ln.qty // 4)
            elif total < floor:
                for ln in lines:
                    ln.qty = ln.qty * 10
            total = sum(ln.qty * ln.unit_price_minor for ln in lines)
            if floor <= total <= cap:
                return lines
        raise RuntimeError(f"could not build lines for {sup.key} within [{floor}, {cap}]")

    def make_po(self, sup: Supplier, lines: list[PoLine], invoice_date: date) -> PurchaseOrder:
        receipts = self.receipts_for(lines, invoice_date)
        return PurchaseOrder(self.next_po_number(), sup.key, sup.currency, lines, receipts)

    def receipts_for(self, lines: list[PoLine], invoice_date: date) -> list[Receipt]:
        """Full delivery, sometimes split in two shipments."""
        when = invoice_date - timedelta(days=self.rng.randrange(3, 21))
        if self.rng.random() < 0.3 and all(ln.qty >= 2 for ln in lines):
            first = {ln.line_no: ln.qty // 2 for ln in lines}
            second = {ln.line_no: ln.qty - ln.qty // 2 for ln in lines}
            return [
                Receipt((when - timedelta(days=4)).isoformat(), first),
                Receipt(when.isoformat(), second),
            ]
        return [Receipt(when.isoformat(), {ln.line_no: ln.qty for ln in lines})]

    def invoice_from_lines(
        self,
        sup: Supplier | None,
        name: str,
        lines: list[PoLine],
        *,
        po_number: str | None,
        currency: str,
        d: date,
        bp: int,
        layout: str,
        tax_id: str,
        address: str,
        bank: str,
        number: str,
    ) -> InvoiceSpec:
        inv_lines = [
            InvLine(ln.line_no, ln.sku, ln.description, ln.qty, ln.unit_price_minor,
                    ln.qty * ln.unit_price_minor)
            for ln in lines
        ]  # fmt: skip
        terms_days = self.rng.choice([30, 30, 45, 60])
        spec = InvoiceSpec(
            id=self.next_id(),
            supplier_key=sup.key if sup else None,
            supplier_name=name,
            supplier_tax_id=tax_id,
            supplier_address=address,
            bank_account=bank,
            layout=layout,
            invoice_number=number,
            invoice_date=d.isoformat(),
            due_date=(d + timedelta(days=terms_days)).isoformat(),
            po_number=po_number,
            currency=currency,
            tax_rate_bp=bp,
            payment_terms=f"Net {terms_days}",
            lines=inv_lines,
            subtotal_minor=0,
            tax_minor=0,
            total_minor=0,
        )
        retotal(spec)
        return spec

    def base(
        self,
        sup: Supplier | None = None,
        *,
        cap: int = INVOICE_CAP_MINOR,
        floor: int = 0,
        register_po: bool = True,
    ) -> tuple[InvoiceSpec, PurchaseOrder]:
        """A clean, fully matching invoice + PO + receipts. Registered unless told otherwise."""
        sup = sup or self.pick_supplier()
        d = self.pick_date()
        lines = self.po_lines_for(sup, cap, floor)
        po = self.make_po(sup, lines, d)
        inv = self.invoice_from_lines(
            sup, sup.name, lines,
            po_number=po.po_number, currency=sup.currency, d=d, bp=sup.tax_rate_bp,
            layout=sup.layout, tax_id=sup.tax_id, address=sup.address, bank=sup.bank_account,
            number=self.next_invoice_number(sup),
        )  # fmt: skip
        if register_po:
            self.pos.append(po)
        return inv, po

    def add(self, inv: InvoiceSpec, code: str | None = None, detail: str = "") -> InvoiceSpec:
        if code:
            inv.planted.append(Planted(code, detail))
        self.invoices.append(inv)
        return inv


def retotal(inv: InvoiceSpec) -> None:
    inv.subtotal_minor = sum(ln.amount_minor for ln in inv.lines)
    inv.tax_minor = tax_of(inv.subtotal_minor, inv.tax_rate_bp)
    inv.total_minor = inv.subtotal_minor + inv.tax_minor


# ----------------------------------------------------------------------------- scenarios


def _clean(b: Builder, n: int) -> None:
    for _ in range(n):
        inv, _po = b.base()
        b.add(inv)


def _line_math(b: Builder) -> None:
    for _ in range(PLAN["LINE_MATH_MISMATCH"]):
        inv, _po = b.base(floor=20_000)
        ln = b.rng.choice([x for x in inv.lines if transpose(x.amount_minor) != x.amount_minor])
        good = ln.amount_minor
        ln.amount_minor = transpose(good)
        retotal(inv)  # totals stay consistent with the (wrong) line, so only the line is off
        b.add(inv, "LINE_MATH_MISMATCH", f"line {ln.line_no}: {good} -> {ln.amount_minor}")


def _total_mismatch(b: Builder) -> None:
    for _ in range(PLAN["TOTAL_MISMATCH"]):
        inv, _po = b.base(floor=20_000)
        good = inv.total_minor
        inv.total_minor = transpose(good)
        b.add(inv, "TOTAL_MISMATCH", f"total {good} -> {inv.total_minor}")


def _tax_mismatch(b: Builder) -> None:
    for _ in range(PLAN["TAX_MISMATCH"]):
        inv, _po = b.base(floor=30_000)
        good = inv.tax_minor
        inv.tax_minor = good + max(500, good // 10)
        inv.total_minor = inv.subtotal_minor + inv.tax_minor
        b.add(inv, "TAX_MISMATCH", f"tax {good} -> {inv.tax_minor}")


def _invalid_date(b: Builder) -> None:
    for i in range(PLAN["INVALID_DATE"]):
        inv, _po = b.base()
        if i < 2:
            d = date(config.FUTURE_YEAR, b.rng.randrange(1, 13), b.rng.randrange(13, 29))
            inv.invoice_date = d.isoformat()
            inv.due_date = (d + timedelta(days=30)).isoformat()
            detail = f"invoice date in the future ({inv.invoice_date})"
        else:
            d = date.fromisoformat(inv.invoice_date)
            inv.due_date = (d - timedelta(days=12)).isoformat()
            detail = f"due date {inv.due_date} is before the invoice date"
        b.add(inv, "INVALID_DATE", detail)


def _unknown_supplier(b: Builder) -> None:
    for name, category, currency in UNKNOWN_SUPPLIERS[: PLAN["UNKNOWN_SUPPLIER"]]:
        stand_in = Supplier(
            key="_unknown", name=name, tax_id=f"{_digits(b.rng, 2)}-{_digits(b.rng, 7)}",
            address=f"{b.rng.randrange(10, 990)} {b.rng.choice(STREETS)}, {b.rng.choice(CITIES[currency])}",
            bank_account=_bank_account(b.rng, currency), currency=currency, tax_rate_bp=800,
            category=category, layout=b.rng.choice("ABCDE"), prefix=name[:2].upper(),
        )  # fmt: skip
        b._inv_seq.setdefault("_unknown", 400)
        d = b.pick_date()
        lines = b.po_lines_for(stand_in, INVOICE_CAP_MINOR)
        inv = b.invoice_from_lines(
            None, name, lines, po_number=None, currency=currency, d=d, bp=800,
            layout=stand_in.layout, tax_id=stand_in.tax_id, address=stand_in.address,
            bank=stand_in.bank_account, number=b.next_invoice_number(stand_in),
        )  # fmt: skip
        inv.may_raise = ["NO_PO", "PO_NOT_FOUND"]
        b.add(inv, "UNKNOWN_SUPPLIER", f"'{name}' is not in the supplier master")


def _bank_changed(b: Builder) -> None:
    for _ in range(PLAN["BANK_DETAILS_CHANGED"]):
        sup = b.by_key["pioneer"]
        inv, _po = b.base(sup)
        # Fraud pattern: same account with the last four digits changed.
        old = inv.bank_account
        inv.bank_account = old[:-4] + f"{(int(old[-4:]) + 3719) % 10000:04d}"
        b.add(inv, "BANK_DETAILS_CHANGED", f"account on file {old} -> {inv.bank_account}")


def _no_po(b: Builder) -> None:
    for _ in range(PLAN["NO_PO"]):
        sup = b.pick_supplier()
        inv, _po = b.base(sup, register_po=False)
        # no open PO for this supplier may match the amount
        assert all(p.total_minor != inv.subtotal_minor for p in b.pos if p.supplier_key == sup.key)
        inv.po_number = None
        b.add(inv, "NO_PO", "invoice has no PO number and no PO matches")


def _po_not_found(b: Builder) -> None:
    for _ in range(PLAN["PO_NOT_FOUND"]):
        inv, _po = b.base(register_po=False)
        inv.po_number = f"PO-9{_digits(b.rng, 4)}"
        inv.may_raise = ["NO_PO"]
        b.add(inv, "PO_NOT_FOUND", f"{inv.po_number} does not exist")


def _price_variance(b: Builder) -> None:
    for _ in range(PLAN["PRICE_VARIANCE"]):
        sup = b.pick_supplier("USD")
        inv, po = b.base(sup, cap=INVOICE_CAP_MINOR // 2, floor=15_000)
        ln = b.rng.choice(inv.lines)
        pct = b.rng.choice([5, 6, 8, 10, 12])
        old = ln.unit_price_minor
        ln.unit_price_minor = old * (100 + pct) // 100
        ln.amount_minor = ln.qty * ln.unit_price_minor
        retotal(inv)
        # Extra, unbilled PO line so the invoice does not exceed the PO total.
        overage = ln.amount_minor - ln.qty * old
        _pad_po(po, overage)
        b.add(inv, "PRICE_VARIANCE", f"line {ln.line_no}: {old} -> {ln.unit_price_minor} (+{pct}%)")


def _qty_variance(b: Builder) -> None:
    for _ in range(PLAN["QTY_VARIANCE"]):
        sup = b.pick_supplier("USD")
        inv, po = b.base(sup, cap=INVOICE_CAP_MINOR // 2, floor=15_000)
        ln = b.rng.choice(inv.lines)
        old = ln.qty
        ln.qty = old + max(1, old // 5)
        ln.amount_minor = ln.qty * ln.unit_price_minor
        retotal(inv)
        # Goods were over-delivered, so the receipt matches the invoice; only qty vs PO differs.
        for r in po.receipts:
            r.qty_by_line[ln.line_no] = r.qty_by_line[ln.line_no] + (
                ln.qty - old if r is po.receipts[-1] else 0
            )
        _pad_po(po, (ln.qty - old) * ln.unit_price_minor)
        b.add(inv, "QTY_VARIANCE", f"line {ln.line_no}: PO qty {old}, invoiced {ln.qty}")


def _pad_po(po: PurchaseOrder, overage: int) -> None:
    n = len(po.lines) + 1
    po.lines.append(
        PoLine(n, f"{po.po_number}-ADD", "Additional item (not yet delivered)", 1, overage * 2)
    )


def _receipt_missing(b: Builder) -> None:
    for _ in range(PLAN["RECEIPT_MISSING"]):
        inv, po = b.base()
        po.receipts = []
        b.add(inv, "RECEIPT_MISSING", f"nothing received for {po.po_number}")


def _qty_not_received(b: Builder) -> None:
    for _ in range(PLAN["QTY_NOT_RECEIVED"]):
        inv, po = b.base(floor=20_000)
        ln = max(po.lines, key=lambda x: x.qty * x.unit_price_minor)
        short = max(1, ln.qty * 3 // 10)
        received = ln.qty - short
        po.receipts = [Receipt(po.receipts[-1].received_at, {x.line_no: x.qty for x in po.lines})]
        po.receipts[0].qty_by_line[ln.line_no] = received
        b.add(inv, "QTY_NOT_RECEIVED", f"line {ln.line_no}: billed {ln.qty}, received {received}")


def _overbilled(b: Builder) -> None:
    for _ in range(PLAN["PO_OVERBILLED"]):
        sup = b.by_key["meridian"]
        d = b.pick_date()
        lines = b.po_lines_for(sup, INVOICE_CAP_MINOR, floor=100_000)
        for ln in lines:
            ln.qty = max(ln.qty, 10)
        po = b.make_po(sup, lines, d)
        b.pos.append(po)

        def part(qtys: list[int]) -> InvoiceSpec:
            part_lines = [replace(ln, qty=q) for ln, q in zip(lines, qtys, strict=True)]
            return b.invoice_from_lines(
                sup, sup.name, part_lines, po_number=po.po_number, currency=sup.currency,
                d=d, bp=sup.tax_rate_bp, layout=sup.layout, tax_id=sup.tax_id,
                address=sup.address, bank=sup.bank_account, number=b.next_invoice_number(sup),
            )  # fmt: skip

        a_q = [round(ln.qty * 0.6) for ln in lines]
        b_q = [ln.qty - aq + max(1, round(ln.qty * 0.2)) for ln, aq in zip(lines, a_q, strict=True)]
        first = b.add(part(a_q))  # clean by itself
        second = part(b_q)
        second.may_raise = ["QTY_VARIANCE", "QTY_NOT_RECEIVED"]
        b.add(second, "PO_OVERBILLED", f"invoices on {po.po_number} exceed the PO total")
        b.after[second.id] = first.id


def _currency_mismatch(b: Builder) -> None:
    for i in range(PLAN["CURRENCY_MISMATCH"]):
        sup = b.pick_supplier("USD") if i != 2 else b.pick_supplier("EUR")
        inv, _po = b.base(sup)
        other = "EUR" if sup.currency == "USD" else "USD"
        if i == 1:
            other = "GBP"
        num, den = FX.get((sup.currency, other), (100, 100))
        for ln in inv.lines:
            ln.unit_price_minor = ln.unit_price_minor * num // den
            ln.amount_minor = ln.qty * ln.unit_price_minor
        inv.currency = other
        retotal(inv)
        inv.may_raise = ["PRICE_VARIANCE", "PO_OVERBILLED"]
        b.add(inv, "CURRENCY_MISMATCH", f"invoice in {other}, PO in {sup.currency}")


def _above_limit(b: Builder) -> None:
    limit = config.TENANT_SETTINGS["approval_amount_limit_minor"]
    for _ in range(PLAN["ABOVE_APPROVAL_LIMIT"]):
        sup = b.pick_supplier("USD")
        inv, _po = b.base(sup, cap=3_000_000, floor=int(limit * 1.15))
        assert inv.total_minor > limit
        b.add(inv, "ABOVE_APPROVAL_LIMIT", f"total {inv.total_minor} above limit {limit}")


def _low_confidence(b: Builder) -> None:
    for field in ["total", "total", "invoice_number", "invoice_date"][
        : PLAN["LOW_CONFIDENCE_FIELD"]
    ]:
        inv, _po = b.base()
        inv.obscure = field
        b.add(inv, "LOW_CONFIDENCE_FIELD", f"{field} is smudged and cannot be read reliably")


def _unreadable(b: Builder) -> None:
    for mode in ["blur", "blank"][: PLAN["UNREADABLE_DOCUMENT"]]:
        inv, _po = b.base()
        inv.unreadable = mode
        inv.may_raise = ["*"]
        b.add(
            inv, "UNREADABLE_DOCUMENT", "document is illegible" if mode == "blur" else "blank page"
        )


def _duplicates(b: Builder) -> None:
    pool = [
        i for i in b.invoices
        if not i.planted and i.supplier_key and not i.may_raise and i.id not in b.after.values()
    ]  # fmt: skip
    seen: set[str] = set()
    originals = []
    for i in pool:
        if i.supplier_key not in seen:
            seen.add(i.supplier_key or "")
            originals.append(i)
    b.rng.shuffle(originals)
    for n, orig in enumerate(originals[: PLAN["POSSIBLE_DUPLICATE"]]):
        dup = replace(
            orig,
            id=b.next_id(),
            lines=[replace(x) for x in orig.lines],
            planted=[],
            may_raise=["PO_OVERBILLED", "QTY_NOT_RECEIVED", "QTY_VARIANCE"],
            duplicate_of=orig.id,
        )
        if n == 2:
            dup.invoice_number = orig.invoice_number.replace("-", "")
        elif n == 3:
            dup.invoice_number = orig.invoice_number.lower()
        elif n == 4:
            head, tail = orig.invoice_number[:-1], orig.invoice_number[-1]
            dup.invoice_number = head + str((int(tail) + 1) % 10)
        b.add(dup, "POSSIBLE_DUPLICATE", f"duplicate of {orig.id} ({orig.invoice_number})")
        b.after[dup.id] = orig.id


# ----------------------------------------------------------------------------- assembly


def _assign_quality(b: Builder) -> None:
    rng = b.rng
    n = len(b.invoices)
    quotas = {q: round(n * share) for q, share in config.QUALITY_MIX.items()}
    quotas["unknown"] = 0
    forced: dict[str, str] = {}
    smudge = [i for i in b.invoices if i.obscure]
    for k, inv in enumerate(smudge):  # smudges only make sense on image documents
        forced[inv.id] = "scanned" if k % 2 == 0 else "photo"
    for inv in b.invoices:
        if inv.unreadable == "blur":
            forced[inv.id] = "photo"
        elif inv.unreadable == "blank":
            forced[inv.id] = "unknown"
    for q in forced.values():
        if q in quotas:
            quotas[q] -= 1
    free = [i for i in b.invoices if i.id not in forced]
    labels = [q for q, c in quotas.items() for _ in range(max(c, 0))]
    while len(labels) < len(free):
        labels.append("clean")
    rng.shuffle(labels)
    for inv, q in zip(free, labels, strict=False):
        inv.quality = q
    for inv in b.invoices:
        if inv.id in forced:
            inv.quality = forced[inv.id]


def _order(b: Builder) -> None:
    rng = b.rng
    invs = list(b.invoices)
    rng.shuffle(invs)
    placed: list[InvoiceSpec] = []
    pending = [i for i in invs if i.id in b.after]
    for i in [i for i in invs if i.id not in b.after]:
        placed.append(i)
    for i in pending:
        idx = next(k for k, p in enumerate(placed) if p.id == b.after[i.id])
        placed.insert(rng.randrange(idx + 1, len(placed) + 1), i)
    for n, inv in enumerate(placed, start=1):
        inv.received_order = n
    b.invoices = placed


def build_world(seed: int = config.SEED) -> World:
    b = Builder(seed)
    _low_confidence(b)
    _unreadable(b)
    _line_math(b)
    _total_mismatch(b)
    _tax_mismatch(b)
    _invalid_date(b)
    _unknown_supplier(b)
    _bank_changed(b)
    _po_not_found(b)
    _price_variance(b)
    _qty_variance(b)
    _receipt_missing(b)
    _qty_not_received(b)
    _overbilled(b)
    _currency_mismatch(b)
    _above_limit(b)
    _clean(b, TOTAL_INVOICES - len(b.invoices) - PLAN["NO_PO"] - PLAN["POSSIBLE_DUPLICATE"])
    _no_po(b)  # after the rest so "no PO matches the amount" is checked against every PO
    _duplicates(b)
    _assign_quality(b)
    _order(b)
    # stable, readable ids in received order
    remap = {inv.id: f"inv-{n:03d}" for n, inv in enumerate(b.invoices, start=1)}
    for inv in b.invoices:
        inv.id = remap[inv.id]
        if inv.duplicate_of:
            inv.duplicate_of = remap[inv.duplicate_of]
    return World(b.suppliers, b.pos, b.invoices)
