import { ArrowLeft, MessageCircleQuestion, ShieldAlert } from "lucide-react";
import Link from "next/link";

import { Money } from "@/components/money";
import { StatusPill } from "@/components/status-pill";
import { Badge } from "@/components/ui/badge";
import type { InvoiceDetail } from "@/lib/api/types";
import { formatDate } from "@/lib/format";
import { ROUTING_REASON_LABELS } from "@/lib/labels";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{label}</dt>
      <dd className="mt-1 truncate text-[15px]">{children}</dd>
    </div>
  );
}

/** Who the invoice is from and what it says, at a glance. */
export function InvoiceHeader({ detail }: { detail: InvoiceDetail }) {
  const h = detail.header;
  return (
    <header className="flex flex-col gap-5">
      <Link href="/queue" className="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> Review queue
      </Link>

      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <StatusPill status={detail.status} />
            {detail.info_requested && (
              <Badge tone="info">
                <MessageCircleQuestion className="size-3" aria-hidden /> Info requested
              </Badge>
            )}
            <Badge>{detail.document.doc_quality} document</Badge>
          </div>
          <h1 className="display text-[2.2rem] font-medium leading-[1.05] tracking-tight sm:text-5xl">
            {h.supplier_name ?? "Supplier not read"}
          </h1>
          <p className="mt-2 font-mono text-[15px] tabular text-muted-foreground">
            {h.invoice_number ?? "no invoice number read"}
          </p>
        </div>
        <div className="text-left sm:text-right">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Total</p>
          <Money display minor={h.total_minor} currency={h.currency} className="text-[2.2rem] font-medium leading-none sm:text-5xl" />
        </div>
      </div>

      <dl className="ledger-rule grid grid-cols-2 gap-x-6 gap-y-4 pb-5 sm:grid-cols-3 lg:grid-cols-6">
        <Fact label="Invoice date">{formatDate(h.invoice_date)}</Fact>
        <Fact label="Due date">{formatDate(h.due_date)}</Fact>
        <Fact label="PO number">
          <span className="font-mono tabular">{h.po_number ?? "—"}</span>
        </Fact>
        <Fact label="Payment terms">{h.payment_terms ?? "—"}</Fact>
        <Fact label="Subtotal">
          <Money minor={h.subtotal_minor} currency={h.currency} />
        </Fact>
        <Fact label="Tax">
          <Money minor={h.tax_minor} currency={h.currency} />
        </Fact>
      </dl>

      {detail.routing_reasons.length > 0 && (detail.status === "needs_review" || detail.status === "cleared") && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="inline-flex items-center gap-1.5 font-medium text-muted-foreground">
            <ShieldAlert className="size-4" aria-hidden /> Sent to a person because
          </span>
          {detail.routing_reasons.map((r) => (
            <Badge key={r} tone="warn" className="normal-case tracking-normal">
              {ROUTING_REASON_LABELS[r] ?? r.replaceAll("_", " ").toLowerCase()}
            </Badge>
          ))}
        </div>
      )}
    </header>
  );
}
