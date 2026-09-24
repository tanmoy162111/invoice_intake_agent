from dataclasses import dataclass, field


@dataclass
class Supplier:
    key: str
    name: str
    tax_id: str
    address: str
    bank_account: str
    currency: str
    tax_rate_bp: int  # basis points: 1500 = 15%
    category: str
    layout: str  # "A".."E"
    prefix: str  # invoice number prefix
    aliases: list[str] = field(default_factory=list)


@dataclass
class PoLine:
    line_no: int
    sku: str
    description: str
    qty: int
    unit_price_minor: int


@dataclass
class Receipt:
    received_at: str  # ISO date
    qty_by_line: dict[int, int]  # PO line_no -> qty


@dataclass
class PurchaseOrder:
    po_number: str
    supplier_key: str
    currency: str
    lines: list[PoLine]
    receipts: list[Receipt]

    @property
    def total_minor(self) -> int:
        return sum(ln.qty * ln.unit_price_minor for ln in self.lines)


@dataclass
class InvLine:
    line_no: int
    sku: str
    description: str
    qty: int
    unit_price_minor: int
    amount_minor: int


@dataclass
class Planted:
    code: str
    detail: str


@dataclass
class InvoiceSpec:
    id: str
    supplier_key: str | None  # None when the supplier is not in the master data
    supplier_name: str  # as printed
    supplier_tax_id: str
    supplier_address: str
    bank_account: str
    layout: str
    invoice_number: str
    invoice_date: str
    due_date: str
    po_number: str | None
    currency: str
    tax_rate_bp: int
    payment_terms: str
    lines: list[InvLine]
    subtotal_minor: int
    tax_minor: int
    total_minor: int
    quality: str = "clean"
    received_order: int = 0
    planted: list[Planted] = field(default_factory=list)
    may_raise: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    obscure: str | None = None  # header field hidden by a smudge
    unreadable: str | None = None  # "blur" | "blank"

    @property
    def must_raise(self) -> list[str]:
        return sorted({p.code for p in self.planted})


@dataclass
class World:
    suppliers: list[Supplier]
    purchase_orders: list[PurchaseOrder]
    invoices: list[InvoiceSpec]
