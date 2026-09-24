"""Renders InvoiceSpec objects to PDF, then degrades them into "scanned" and "photo" versions.

Five layouts differ in wording, field positions, columns, date and number formats, so that
extraction has to generalise instead of memorising one template.
"""

import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .model import InvoiceSpec

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]  # fmt: skip


@dataclass
class Layout:
    name: str
    font: str
    bold: str
    accent: str
    title: str
    title_side: str  # "left" | "right"
    meta_style: str  # "list" | "band"
    date_fmt: str  # "iso" | "dmy_long" | "mdy_long" | "dmy_dots" | "dmy_dash"
    labels: dict[str, str]
    columns: list[str]  # subset/order of: desc, sku, qty, price, tax, amount
    col_titles: dict[str, str]
    totals: tuple[str, str, str]  # subtotal, tax, total labels
    table_style: str  # "lines" | "banded" | "grid" | "plain"
    show_bank: bool
    show_tax_id: bool
    show_sku: bool
    show_line_tax: bool
    po_in_notes: bool = False
    tax_label_with_rate: bool = True
    boxed_total: bool = False


LAYOUTS: dict[str, Layout] = {
    "A": Layout(
        "A",
        "Helvetica",
        "Helvetica-Bold",
        "#1f4e79",
        "INVOICE",
        "right",
        "list",
        "iso",
        {
            "number": "Invoice #",
            "date": "Invoice Date",
            "due": "Due Date",
            "po": "PO Number",
            "terms": "Terms",
            "tax_id": "Tax ID",
            "bank": "Bank details",
        },
        ["desc", "qty", "price", "amount"],
        {"desc": "Description", "qty": "Qty", "price": "Unit Price", "amount": "Amount"},
        ("Subtotal", "Tax", "Total"),
        "lines",
        True,
        True,
        False,
        False,
    ),  # fmt: skip
    "B": Layout(
        "B",
        "Helvetica",
        "Helvetica-Bold",
        "#444444",
        "Tax Invoice",
        "left",
        "band",
        "dmy_long",
        {
            "number": "Inv No",
            "date": "Date",
            "due": "Due",
            "po": "Your Ref (PO)",
            "terms": "Payment",
            "tax_id": "Tax ID",
            "bank": "Remit to",
        },
        ["desc", "qty", "price", "amount"],
        {"desc": "Item", "qty": "Qty", "price": "Rate", "amount": "Total"},
        ("Net", "VAT/Tax", "Amount Due"),
        "plain",
        True,
        False,
        False,
        False,
        boxed_total=True,
    ),  # fmt: skip
    "C": Layout(
        "C",
        "Times-Roman",
        "Times-Bold",
        "#5b2c83",
        "BILL",
        "left",
        "list",
        "mdy_long",
        {
            "number": "Bill No.",
            "date": "Bill Date",
            "due": "Payment Due",
            "po": "Order Ref",
            "terms": "Terms",
            "tax_id": "VAT / EIN",
            "bank": "Bank",
        },
        ["sku", "desc", "qty", "price", "amount"],
        {
            "sku": "Item Code",
            "desc": "Particulars",
            "qty": "Units",
            "price": "Price",
            "amount": "Line Total",
        },
        ("Sub-total", "Sales Tax", "Grand Total"),
        "grid",
        True,
        True,
        True,
        False,
    ),  # fmt: skip
    "D": Layout(
        "D",
        "Helvetica",
        "Helvetica-Bold",
        "#0b7a75",
        "INVOICE",
        "right",
        "band",
        "dmy_dots",
        {
            "number": "INVOICE NO.",
            "date": "ISSUED",
            "due": "DUE",
            "po": "PURCHASE ORDER",
            "terms": "TERMS",
            "tax_id": "TAX NO.",
            "bank": "PAYMENT DETAILS",
        },
        ["desc", "sku", "qty", "price", "tax", "amount"],
        {
            "desc": "DESCRIPTION",
            "sku": "SKU",
            "qty": "QTY",
            "price": "PRICE",
            "tax": "TAX %",
            "amount": "AMOUNT",
        },
        ("SUBTOTAL", "TAX", "TOTAL DUE"),
        "banded",
        True,
        True,
        True,
        True,
        boxed_total=True,
    ),  # fmt: skip
    "E": Layout(
        "E",
        "Courier",
        "Courier-Bold",
        "#000000",
        "Invoice",
        "left",
        "list",
        "dmy_dash",
        {
            "number": "Invoice number:",
            "date": "Invoice date:",
            "due": "Due date:",
            "po": "Ref:",
            "terms": "Terms:",
            "tax_id": "",
            "bank": "",
        },
        ["desc", "qty", "price", "amount"],
        {"desc": "Description", "qty": "Qty", "price": "Each", "amount": "Total"},
        ("Subtotal:", "Tax:", "TOTAL:"),
        "plain",
        False,
        False,
        False,
        False,
        po_in_notes=True,
        tax_label_with_rate=False,
    ),  # fmt: skip
}


# ----------------------------------------------------------------------------- formatting


def fmt_amount(minor: int, currency: str) -> str:
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(minor), 100)
    if currency == "EUR":
        return f"{sign}{whole:,}".replace(",", ".") + f",{frac:02d} EUR"
    symbol = {"USD": "$", "GBP": "£"}[currency]
    return f"{sign}{symbol}{whole:,}.{frac:02d}"


def fmt_date(iso: str, style: str) -> str:
    d = date.fromisoformat(iso)
    return {
        "iso": iso,
        "dmy_long": f"{d.day} {MONTHS[d.month - 1]} {d.year}",
        "mdy_long": f"{MONTHS_LONG[d.month - 1]} {d.day}, {d.year}",
        "dmy_dots": f"{d.day:02d}.{d.month:02d}.{d.year}",
        "dmy_dash": f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}",
    }[style]


def fmt_rate(bp: int) -> str:
    pct = bp / 100
    return f"{pct:g}%"


# ----------------------------------------------------------------------------- drawing


@dataclass
class Canvas:
    c: canvas.Canvas
    cfg: Layout
    boxes: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)

    def put(self, x: float, y: float, text: str, *, size: float = 9, bold: bool = False,
            align: str = "left", key: str | None = None, color: Any = black) -> None:  # fmt: skip
        font = self.cfg.bold if bold else self.cfg.font
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        w = stringWidth(text, font, size)
        x0 = {"left": x, "right": x - w, "center": x - w / 2}[align]
        self.c.drawString(x0, y, text)
        if key:
            self.boxes[key] = (x0 - 1, y - 2, x0 + w + 1, y + size)


def _fit(text: str, font: str, size: float, width: float) -> str:
    while text and stringWidth(text, font, size) > width:
        text = text[:-1]
    return text


def draw_invoice(
    inv: InvoiceSpec, cfg: Layout, path: Path
) -> dict[str, tuple[float, float, float, float]]:
    pagesize = LETTER if inv.currency == "USD" else A4
    W, H = pagesize
    c = canvas.Canvas(str(path), pagesize=pagesize, invariant=1)
    cv = Canvas(c, cfg)
    accent = HexColor(cfg.accent)
    M = 48.0
    cur = inv.currency

    # --- title and supplier block
    title_x, title_align = (W - M, "right") if cfg.title_side == "right" else (M, "left")
    sup_x, sup_align = (M, "left") if cfg.title_side == "right" else (W - M, "right")
    cv.put(title_x, H - M - 14, cfg.title, size=24, bold=True, align=title_align, color=accent)
    y = H - M - 10
    cv.put(sup_x, y, inv.supplier_name, size=12, bold=True, align=sup_align)
    y -= 14
    cv.put(sup_x, y, inv.supplier_address, size=9, align=sup_align)
    if cfg.show_tax_id:
        y -= 12
        cv.put(sup_x, y, f"{cfg.labels['tax_id']}: {inv.supplier_tax_id}", size=9, align=sup_align)

    # --- meta block
    meta: list[tuple[str, str, str | None]] = [
        (cfg.labels["number"], inv.invoice_number, "invoice_number"),
        (cfg.labels["date"], fmt_date(inv.invoice_date, cfg.date_fmt), "invoice_date"),
        (cfg.labels["due"], fmt_date(inv.due_date, cfg.date_fmt), None),
    ]
    if inv.po_number and not cfg.po_in_notes:
        meta.append((cfg.labels["po"], inv.po_number, "po_number"))
    meta.append((cfg.labels["terms"], inv.payment_terms, None))
    top = H - 150
    if cfg.meta_style == "list":
        mx = W - M - 190 if cfg.title_side == "right" else W - M - 190
        yy = top
        for label, value, key in meta:
            cv.put(mx, yy, label, size=9, bold=True)
            cv.put(mx + 100, yy, value, size=9, key=key)
            yy -= 14
        table_top = min(yy, top - 14 * 5) - 24
    else:
        colw = (W - 2 * M) / len(meta)
        c.setStrokeColor(accent)
        c.setLineWidth(0.8)
        c.line(M, top + 14, W - M, top + 14)
        for n, (label, value, key) in enumerate(meta):
            cv.put(M + n * colw, top, label, size=7.5, bold=True, color=accent)
            cv.put(M + n * colw, top - 14, value, size=10, key=key)
        c.line(M, top - 26, W - M, top - 26)
        table_top = top - 60

    # --- lines table
    widths = {"desc": 0.42, "sku": 0.14, "qty": 0.08, "price": 0.15, "tax": 0.08, "amount": 0.16}
    cols = cfg.columns
    total_w = sum(widths[k] for k in cols)
    span = W - 2 * M
    xs: dict[str, tuple[float, float]] = {}
    x = M
    for k in cols:
        w = widths[k] / total_w * span
        xs[k] = (x, x + w)
        x += w
    row_h = 20
    if cfg.table_style in {"banded", "grid"}:
        c.setFillColor(accent)
        c.rect(M, table_top - 4, span, row_h, stroke=0, fill=1)
        head_color = white
    else:
        head_color = black
    for k in cols:
        x0, x1 = xs[k]
        right = k in {"qty", "price", "tax", "amount"}
        cv.put(x1 - 4 if right else x0 + 4, table_top + 2, cfg.col_titles[k], size=8.5, bold=True,
               align="right" if right else "left", color=head_color)  # fmt: skip
    if cfg.table_style == "lines":
        c.setStrokeColor(black)
        c.line(M, table_top - 6, W - M, table_top - 6)
    y = table_top - 6 - row_h + 6
    for n, ln in enumerate(inv.lines):
        if cfg.table_style == "banded" and n % 2 == 0:
            c.setFillColor(Color(0.94, 0.96, 0.96))
            c.rect(M, y - 5, span, row_h, stroke=0, fill=1)
        cells = {
            "desc": _fit(ln.description, cfg.font, 9, xs["desc"][1] - xs["desc"][0] - 8),
            "sku": ln.sku,
            "qty": str(ln.qty),
            "price": fmt_amount(ln.unit_price_minor, cur),
            "tax": fmt_rate(inv.tax_rate_bp),
            "amount": fmt_amount(ln.amount_minor, cur),
        }
        for k in cols:
            x0, x1 = xs[k]
            right = k in {"qty", "price", "tax", "amount"}
            cv.put(x1 - 4 if right else x0 + 4, y, cells[k], size=9,
                   align="right" if right else "left")  # fmt: skip
        if cfg.table_style == "grid":
            c.setStrokeColor(Color(0.6, 0.6, 0.6))
            c.line(M, y - 5, W - M, y - 5)
        y -= row_h
    if cfg.table_style in {"lines", "grid"}:
        c.setStrokeColor(black)
        c.line(M, y + row_h - 5, W - M, y + row_h - 5)

    # --- totals
    sub_l, tax_l, tot_l = cfg.totals
    if cfg.tax_label_with_rate:
        tax_l = f"{tax_l} ({fmt_rate(inv.tax_rate_bp)})"
    ty = y - 14
    lx, vx = W - M - 170, W - M - 4
    cv.put(lx, ty, sub_l, size=9)
    cv.put(vx, ty, fmt_amount(inv.subtotal_minor, cur), size=9, align="right")
    ty -= 14
    cv.put(lx, ty, tax_l, size=9)
    cv.put(vx, ty, fmt_amount(inv.tax_minor, cur), size=9, align="right")
    ty -= 22
    if cfg.boxed_total:
        c.setStrokeColor(accent)
        c.setLineWidth(1.2)
        c.rect(lx - 8, ty - 7, 178, 24, stroke=1, fill=0)
    cv.put(lx, ty, tot_l, size=11, bold=True)
    cv.put(vx, ty, fmt_amount(inv.total_minor, cur), size=11, bold=True, align="right", key="total")

    # --- footer
    fy = 150.0
    if cfg.po_in_notes and inv.po_number:
        cv.put(M, fy + 30, f"{cfg.labels['po']} {inv.po_number}", size=9)
    if cfg.show_bank:
        cv.put(M, fy, cfg.labels["bank"], size=9, bold=True)
        cv.put(M, fy - 13, inv.bank_account, size=9)
    cv.put(M, 70, f"Payment terms: {inv.payment_terms}. Thank you for your business.", size=8)
    c.showPage()
    c.save()
    return cv.boxes


# ----------------------------------------------------------------------------- degradation


def _page_image(pdf_path: Path, dpi: int) -> Image.Image:
    doc = pdfium.PdfDocument(str(pdf_path))
    img: Image.Image = doc[0].render(scale=dpi / 72).to_pil().convert("RGB")
    doc.close()
    return img


def _noise(img: Image.Image, rng: np.random.Generator, sigma: float) -> Image.Image:
    arr = np.asarray(img, dtype=np.float32)
    arr += rng.normal(0, sigma, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _smudge(img: Image.Image, box: tuple[float, float, float, float], scale: float, page_h: float,
            rng: random.Random) -> Image.Image:  # fmt: skip
    x0, y0, x1, y1 = box
    pad = 5
    left, right = x0 * scale - pad, x1 * scale + pad
    top, bottom = (page_h - y1) * scale - pad, (page_h - y0) * scale + pad
    mask = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((left - 8, top - 4, right + 8, bottom + 4), fill=235)
    mask = mask.filter(ImageFilter.GaussianBlur(2.5))
    ink = Image.new("RGB", img.size, (55 + rng.randrange(20), 50, 45))
    return Image.composite(ink, img, mask)


def _coeffs(dst: list[tuple[float, float]], src: list[tuple[float, float]]) -> list[float]:
    rows, rhs = [], []
    for (x, y), (X, Y) in zip(dst, src, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -X * x, -X * y])
        rhs.append(X)
        rows.append([0, 0, 0, x, y, 1, -Y * x, -Y * y])
        rhs.append(Y)
    return [
        float(v) for v in np.linalg.solve(np.array(rows, dtype=float), np.array(rhs, dtype=float))
    ]


def make_scanned(pdf: Path, out: Path, seed: int, *, box: tuple[float, float, float, float] | None,
                 page_h: float) -> None:  # fmt: skip
    rng, nrng = random.Random(seed), np.random.default_rng(seed)
    dpi = 110
    img = _page_image(pdf, dpi)
    if box:
        img = _smudge(img, box, dpi / 72, page_h, rng)
    img = img.convert("L")
    img = img.rotate(rng.uniform(-1.6, 1.6), resample=Image.BICUBIC, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    arr = np.asarray(img, dtype=np.float32)
    arr = 20 + arr * 0.9  # washed-out blacks, slightly grey paper
    edge = np.linspace(0, 1, arr.shape[1], dtype=np.float32)
    arr -= 18 * (edge[None, :] ** 6)  # scanner shadow on one edge
    img = _noise(Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)), nrng, 5)
    img.save(out, "PDF", resolution=dpi, quality=55)


def make_photo(pdf: Path, out: Path, seed: int, *, box: tuple[float, float, float, float] | None,
               page_h: float) -> None:  # fmt: skip
    rng, nrng = random.Random(seed), np.random.default_rng(seed)
    dpi = 100
    page = _page_image(pdf, dpi)
    if box:
        page = _smudge(page, box, dpi / 72, page_h, rng)
    w, h = page.size
    pad = int(0.09 * w)
    cw, ch = w + 2 * pad, h + 2 * pad
    jit = lambda: rng.uniform(-0.07, 0.07) * w  # noqa: E731
    dst = [(pad + jit(), pad + jit()), (pad + w + jit(), pad + jit()),
           (pad + w + jit(), pad + h + jit()), (pad + jit(), pad + h + jit())]  # fmt: skip
    src = [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
    coeffs = _coeffs(dst, src)
    warped = page.transform((cw, ch), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC)
    mask = Image.new("L", (w, h), 255).transform((cw, ch), Image.PERSPECTIVE, coeffs)
    desk = Image.new("RGB", (cw, ch), (92 + rng.randrange(20), 74, 58))
    desk = _noise(desk, nrng, 10)
    img = Image.composite(warped, desk, mask)
    # uneven lighting: a diagonal gradient plus a soft hot spot
    ang = rng.uniform(0, 6.28)
    yy, xx = np.mgrid[0:ch, 0:cw].astype(np.float32)
    grad = (xx / cw) * np.cos(ang) + (yy / ch) * np.sin(ang)
    light = 0.55 + 0.5 * (grad - grad.min()) / (grad.max() - grad.min())
    arr = np.asarray(img, dtype=np.float32) * light[:, :, None]
    arr *= np.array([1.0, 0.97, 0.9], dtype=np.float32)  # warm colour cast
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(1.1)
    )
    _noise(img, nrng, 7).save(out, "JPEG", quality=55)


def make_unreadable_blur(pdf: Path, out: Path, seed: int) -> None:
    nrng = np.random.default_rng(seed)
    img = _page_image(pdf, 90).convert("L").filter(ImageFilter.GaussianBlur(7))
    arr = 120 + np.asarray(img, dtype=np.float32) * 0.45  # low contrast
    _noise(Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)), nrng, 18).save(
        out, "JPEG", quality=45
    )


def make_blank(out: Path, seed: int) -> None:
    nrng = np.random.default_rng(seed)
    img = Image.new("L", (935, 1210), 244)
    _noise(img, nrng, 4).save(out, "PDF", resolution=110, quality=50)


def sha256_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
