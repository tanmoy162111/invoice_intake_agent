"""Generate the synthetic dataset.

    PYTHONPATH=data uv run --project apps/api --group generator python -m generator.generate

Writes data/seed/ (all invoices, ground truth, master data, manifest) and data/golden/
(a fixed evaluation subset). Golden files are created once and never overwritten.
"""

import json
import math
import random
import shutil
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from . import config, render
from .model import InvoiceSpec, World
from .world import build_world

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "data" / "seed"
GOLDEN_DIR = ROOT / "data" / "golden"
NS = uuid.uuid5(uuid.NAMESPACE_DNS, config.NAMESPACE)


def stable_id(kind: str, key: str) -> str:
    return str(uuid.uuid5(NS, f"{kind}:{key}"))


# ----------------------------------------------------------------------------- truth


def truth_for(inv: InvoiceSpec, world: World, filename: str, split: str) -> dict[str, Any]:
    cfg = render.LAYOUTS[inv.layout]
    readable = inv.unreadable is None
    header: dict[str, Any] = {
        "supplier_name": inv.supplier_name,
        "supplier_tax_id": inv.supplier_tax_id if cfg.show_tax_id else None,
        "supplier_address": inv.supplier_address,
        "supplier_bank_account": inv.bank_account if cfg.show_bank else None,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date,
        "due_date": inv.due_date,
        "po_number": inv.po_number,
        "currency": inv.currency,
        "subtotal_minor": inv.subtotal_minor,
        "tax_total_minor": inv.tax_minor,
        "total_minor": inv.total_minor,
        "payment_terms": inv.payment_terms,
    }
    lines = [
        {
            "line_no": ln.line_no,
            "description": ln.description,
            "sku": ln.sku if cfg.show_sku else None,
            "quantity": ln.qty,
            "unit_price_minor": ln.unit_price_minor,
            "amount_minor": ln.amount_minor,
            "tax_rate_bp": inv.tax_rate_bp if cfg.show_line_tax else None,
        }
        for ln in inv.lines
    ]
    if not readable:
        header = dict.fromkeys(header)
        lines = []
    return {
        "id": inv.id,
        "file": filename,
        "split": split,
        "doc_quality": inv.quality,
        "layout": inv.layout,
        "received_order": inv.received_order,
        "readable": readable,
        "supplier_key": inv.supplier_key,
        "supplier_on_file": inv.supplier_key is not None,
        "header": header,
        "lines": lines,
        "obscured_field": inv.obscure,
        "duplicate_of": inv.duplicate_of,
        "expected": {
            "must_raise": inv.must_raise,
            "may_raise": inv.may_raise,
            "should_clear": not inv.planted,
        },
        "planted": [{"code": p.code, "detail": p.detail} for p in inv.planted],
    }


# ----------------------------------------------------------------------------- golden


def select_golden(world: World) -> set[str]:
    """Fixed rule: half of the invoices for every planted code (rounded up), plus their
    dependencies, topped up with clean invoices until GOLDEN_SIZE."""
    rng = random.Random(config.SEED + 1)
    chosen: set[str] = set()
    by_id = {i.id: i for i in world.invoices}
    codes = sorted({c for i in world.invoices for c in i.must_raise})
    for code in codes:
        pool = [i.id for i in world.invoices if code in i.must_raise]
        rng.shuffle(pool)
        chosen.update(pool[: math.ceil(len(pool) / 2)])
    for iid in sorted(chosen):
        inv = by_id[iid]
        if inv.duplicate_of:
            chosen.add(inv.duplicate_of)
        if "PO_OVERBILLED" in inv.must_raise:
            chosen.update(i.id for i in world.invoices if i.po_number == inv.po_number)
    clean = [i.id for i in world.invoices if not i.planted and i.id not in chosen]
    rng.shuffle(clean)
    for iid in clean:
        if len(chosen) >= config.GOLDEN_SIZE:
            break
        chosen.add(iid)
    return chosen


# ----------------------------------------------------------------------------- files


def file_name(inv: InvoiceSpec, taken: set[str]) -> str:
    n = inv.received_order
    if inv.quality == "clean":
        slug = (inv.supplier_name.split()[0] if inv.supplier_name else "invoice").replace(".", "")
        base, ext = f"{slug}-{inv.invoice_number}", "pdf"
    elif inv.quality == "photo":
        base, ext = f"IMG_{4000 + n * 7:04d}", "jpg"
    else:
        base, ext = f"scan{n:04d}", "pdf"
    name, k = f"{base}.{ext}", 1
    while name in taken:
        name, k = f"{base} ({k}).{ext}", k + 1
    taken.add(name)
    return name


def render_invoice(inv: InvoiceSpec, dest: Path, tmp: Path) -> None:
    seed = config.SEED + int(inv.id.split("-")[1])
    if inv.unreadable == "blank":
        render.make_blank(dest, seed)
        return
    cfg = render.LAYOUTS[inv.layout]
    pdf = tmp / f"{inv.id}.pdf"
    boxes = render.draw_invoice(inv, cfg, pdf)
    page_h = 792.0 if inv.currency == "USD" else 841.89
    box = boxes.get(inv.obscure) if inv.obscure else None
    if inv.unreadable == "blur":
        render.make_unreadable_blur(pdf, dest, seed)
    elif inv.quality == "clean":
        shutil.copyfile(pdf, dest)
    elif inv.quality == "scanned":
        render.make_scanned(pdf, dest, seed, box=box, page_h=page_h)
    else:
        render.make_photo(pdf, dest, seed, box=box, page_h=page_h)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


def master_data(world: World) -> dict[str, Any]:
    return {
        "tenant": {
            "id": config.TENANT_ID,
            "name": config.TENANT_NAME,
            "settings": config.TENANT_SETTINGS,
        },
        "suppliers": [
            {
                "id": stable_id("supplier", s.key),
                "key": s.key,
                "name": s.name,
                "aliases": s.aliases,
                "tax_id": s.tax_id,
                "bank_account": s.bank_account,
                "default_currency": s.currency,
                "tax_rate_bp": s.tax_rate_bp,
            }
            for s in world.suppliers
        ],
        "purchase_orders": [
            {
                "id": stable_id("po", po.po_number),
                "po_number": po.po_number,
                "supplier_key": po.supplier_key,
                "currency": po.currency,
                "total_minor": po.total_minor,
                "lines": [
                    {
                        "id": stable_id("po_line", f"{po.po_number}:{ln.line_no}"),
                        "line_no": ln.line_no,
                        "sku": ln.sku,
                        "description": ln.description,
                        "qty": ln.qty,
                        "unit_price_minor": ln.unit_price_minor,
                    }
                    for ln in po.lines
                ],
                "receipts": [
                    {
                        "id": stable_id("receipt", f"{po.po_number}:{n}"),
                        "received_at": r.received_at,
                        "lines": [
                            {"line_no": line_no, "qty_received": q}
                            for line_no, q in sorted(r.qty_by_line.items())
                        ],
                    }
                    for n, r in enumerate(po.receipts)
                ],
            }
            for po in world.purchase_orders
        ],
    }


def manifest_md(world: World, rows: list[dict[str, Any]]) -> str:
    n = len(rows)
    quality = Counter(r["doc_quality"] for r in rows)
    codes = Counter(c for r in rows for c in r["must_raise"])
    out = [
        "# Synthetic dataset manifest",
        "",
        "Generated by `data/generator` (seed "
        f"{config.SEED}). Do not edit by hand: run `make generate`.",
        "",
        f"- Invoices: **{n}** from **{len(world.suppliers)}** suppliers, "
        f"**{len(world.purchase_orders)}** purchase orders",
        "- Quality mix: " + ", ".join(f"{k} {v} ({v / n:.0%})" for k, v in sorted(quality.items())),
        f"- Golden (fixed eval) set: **{sum(1 for r in rows if r['split'] == 'golden')}** invoices "
        "(copies live in `data/golden/`)",
        "- `must_raise`: planted problems the pipeline has to flag. `may_raise`: knock-on codes that "
        "are acceptable (`*` = anything, the document is unreadable). An invoice with no "
        "`must_raise` should be cleared.",
        "",
        "## Coverage by exception code",
        "",
        "| Code | Invoices |",
        "|---|---|",
    ]
    out += [f"| `{c}` | {codes[c]} |" for c in sorted(codes)]
    out += [
        "",
        "## Invoices",
        "",
        "| ID | File | Layout | Quality | Split | Supplier | Invoice no. | Total | Planted problems |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        planted = ", ".join(f"`{c}`" for c in r["must_raise"]) or "none (clean)"
        out.append(
            f"| {r['id']} | {r['file']} | {r['layout']} | {r['doc_quality']} | {r['split']} | "
            f"{r['supplier']} | {r['invoice_number']} | {r['total']} | {planted} |"
        )
    return "\n".join(out) + "\n"


GOLDEN_SOURCES = """# Golden set sources

All documents in this folder are **synthetic**. They are generated by `data/generator/` and
copied here by `make generate` (a subset chosen by a fixed rule; see `select_golden`).
No third-party or licensed public invoice samples are included.

If licensed public samples are added later, record each one here with its source URL and
licence, and only if the licence is clearly compatible (playbook §8.1).

**Do not edit or regenerate files in this folder.** The golden set is fixed so results stay
comparable over time. The generator never overwrites an existing golden file.
"""


def main() -> None:
    world = build_world()
    golden_ids = select_golden(world)
    for d in (
        SEED_DIR / "invoices",
        SEED_DIR / "truth",
        GOLDEN_DIR / "invoices",
        GOLDEN_DIR / "truth",
    ):
        d.mkdir(parents=True, exist_ok=True)
    taken: set[str] = set()
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for inv in world.invoices:
            fname = file_name(inv, taken)
            split = "golden" if inv.id in golden_ids else "dev"
            dest = SEED_DIR / "invoices" / fname
            render_invoice(inv, dest, tmp)
            truth = truth_for(inv, world, fname, split)
            write_json(SEED_DIR / "truth" / f"{inv.id}.json", truth)
            if split == "golden":
                for sub, src in (
                    ("invoices", dest),
                    ("truth", SEED_DIR / "truth" / f"{inv.id}.json"),
                ):
                    target = GOLDEN_DIR / sub / src.name
                    if not target.exists():  # create-only: golden files are never overwritten
                        shutil.copyfile(src, target)
            rows.append(
                {
                    "id": inv.id,
                    "file": fname,
                    "layout": inv.layout,
                    "doc_quality": inv.quality,
                    "split": split,
                    "supplier": inv.supplier_name,
                    "invoice_number": inv.invoice_number,
                    "total": render.fmt_amount(inv.total_minor, inv.currency),
                    "must_raise": inv.must_raise,
                    "may_raise": inv.may_raise,
                }
            )
    write_json(SEED_DIR / "master.json", master_data(world))
    write_json(SEED_DIR / "manifest.json", rows)
    (SEED_DIR / "MANIFEST.md").write_text(manifest_md(world, rows))
    (GOLDEN_DIR / "SOURCES.md").write_text(GOLDEN_SOURCES)
    print(f"wrote {len(rows)} invoices ({len(golden_ids)} golden) to {SEED_DIR}")


if __name__ == "__main__":
    main()
