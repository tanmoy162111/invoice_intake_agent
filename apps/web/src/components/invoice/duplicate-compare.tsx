import { CheckCheck, ExternalLink } from "lucide-react";
import Link from "next/link";

import { Money } from "@/components/money";
import { StatusPill } from "@/components/status-pill";
import type { InvoiceDetail, RelatedInvoice } from "@/lib/api/types";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

type Row = { label: string; a: React.ReactNode; b: React.ReactNode; same: boolean };

function rows(current: InvoiceDetail, other: RelatedInvoice): Row[] {
  const h = current.header;
  const same = (x: unknown, y: unknown) => String(x ?? "") === String(y ?? "");
  const mono = (v: string | null | undefined) => <span className="font-mono tabular">{v ?? "—"}</span>;
  return [
    { label: "Supplier", a: h.supplier_name ?? "—", b: other.supplier_name ?? "—", same: same(h.supplier_name, other.supplier_name) },
    { label: "Invoice number", a: mono(h.invoice_number), b: mono(other.invoice_number), same: same(h.invoice_number, other.invoice_number) },
    { label: "Invoice date", a: formatDate(h.invoice_date), b: formatDate(other.invoice_date), same: same(h.invoice_date, other.invoice_date) },
    {
      label: "Total",
      a: <Money minor={h.total_minor} currency={h.currency} />,
      b: <Money minor={other.total_minor} currency={other.currency} />,
      same: same(h.total_minor, other.total_minor) && same(h.currency, other.currency),
    },
  ];
}

/** The two invoices side by side, with what differs marked, so a person can tell a copy from a coincidence. */
export function DuplicateCompare({ current, other }: { current: InvoiceDetail; other: RelatedInvoice }) {
  const data = rows(current, other);
  return (
    <div className="mt-4 overflow-hidden rounded-md border bg-background/60">
      <div className="grid grid-cols-[6.5rem_1fr_1fr] gap-x-3 border-b bg-muted/50 px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground sm:grid-cols-[8rem_1fr_1fr]">
        <span />
        <span>This invoice</span>
        <span className="flex items-center gap-2">
          Earlier <StatusPill status={other.status} />
        </span>
      </div>
      <dl>
        {data.map((r) => (
          <div key={r.label} className="grid grid-cols-[6.5rem_1fr_1fr] items-center gap-x-3 border-b px-3 py-2 text-sm last:border-b-0 sm:grid-cols-[8rem_1fr_1fr]">
            <dt className="text-[13px] text-muted-foreground">{r.label}</dt>
            <dd className={cn("min-w-0 break-words", !r.same && "font-semibold")}>{r.a}</dd>
            <dd className={cn("flex min-w-0 items-center gap-1.5 break-words", !r.same && "font-semibold")}>
              {r.b}
              {r.same ? (
                <CheckCheck className="size-3.5 shrink-0 text-block" aria-label="Same as this invoice" />
              ) : (
                <span className="rounded-sm bg-review-soft px-1 text-[10px] font-bold uppercase text-review">differs</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <Link
        href={`/invoices/${other.id}`}
        className="flex items-center justify-center gap-2 border-t bg-muted/40 px-3 py-2.5 text-sm font-medium hover:bg-muted"
      >
        Open the earlier invoice <ExternalLink className="size-3.5" aria-hidden />
      </Link>
    </div>
  );
}
