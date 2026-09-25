import { Link2, Link2Off } from "lucide-react";

import { Money } from "@/components/money";
import type { InvoiceDetail } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export function LinesPanel({ detail }: { detail: InvoiceDetail }) {
  const currency = detail.header.currency;
  if (detail.lines.length === 0) {
    return <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">No lines were read from this invoice.</p>;
  }
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <div
        aria-hidden
        className="ledger-rule hidden grid-cols-[2rem_minmax(0,1fr)_4.5rem_6.5rem_7rem_1.5rem] gap-x-3 bg-muted/50 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground md:grid"
      >
        <span>#</span>
        <span>Description</span>
        <span className="text-right">Qty</span>
        <span className="text-right">Unit price</span>
        <span className="text-right">Amount</span>
        <span />
      </div>
      <ul className="divide-y">
        {detail.lines.map((l) => (
          <li
            key={l.line_no}
            className="grid gap-x-3 gap-y-1 px-4 py-3 md:grid-cols-[2rem_minmax(0,1fr)_4.5rem_6.5rem_7rem_1.5rem] md:items-center"
          >
            <span className="font-mono text-xs tabular text-muted-foreground">{l.line_no}</span>
            <div className="min-w-0">
              <p className="break-words text-sm">{l.description ?? "—"}</p>
              {l.sku && <p className="font-mono text-xs text-muted-foreground">{l.sku}</p>}
            </div>
            <span className="font-mono text-sm tabular md:text-right">
              <span className="mr-1 text-xs text-muted-foreground md:hidden">Qty</span>
              {l.qty ?? "—"}
            </span>
            <Money minor={l.unit_price_minor} currency={currency} className="text-sm md:text-right" />
            <Money minor={l.amount_minor} currency={currency} className="text-sm font-medium md:text-right" />
            <span className={cn("hidden md:block", l.matched ? "text-ok" : "text-muted-foreground")} title={l.matched ? "Matched to a purchase order line" : "Not matched to a purchase order line"}>
              {l.matched ? <Link2 className="size-4" aria-label="Matched to a PO line" /> : <Link2Off className="size-4" aria-label="Not matched to a PO line" />}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
