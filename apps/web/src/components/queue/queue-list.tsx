import { ChevronRight, MessageCircleQuestion } from "lucide-react";
import Link from "next/link";

import { Money } from "@/components/money";
import { SeverityStamp } from "@/components/severity-stamp";
import { StatusPill } from "@/components/status-pill";
import { Badge } from "@/components/ui/badge";
import type { QueueItem } from "@/lib/api/types";
import { formatAge, formatDate } from "@/lib/format";
import { EXCEPTION_LABELS } from "@/lib/labels";
import { queueHref, type QueueParams } from "@/lib/queue-params";

function Row({ item, index, params }: { item: QueueItem; index: number; params: QueueParams }) {
  const top = item.top_exception;
  const more = item.open_exceptions - 1;
  return (
    <li
      style={{ "--i": Math.min(index, 12) } as React.CSSProperties}
      className="reveal group relative grid gap-x-6 gap-y-2 border-b px-4 py-4 transition-colors last:border-b-0 hover:bg-muted/60 md:grid-cols-[6.5rem_minmax(0,1.3fr)_minmax(0,1fr)_9rem_4.5rem_1.25rem] md:items-center md:px-5"
    >
      <div className="flex items-center gap-2">
        {top ? <SeverityStamp severity={top.severity} /> : <StatusPill status={item.status} />}
      </div>

      <div className="min-w-0">
        <Link
          href={`/invoices/${item.id}`}
          className="display block truncate text-[1.15rem] font-medium leading-snug outline-none after:absolute after:inset-0 focus-visible:after:ring-2 focus-visible:after:ring-ring"
        >
          {item.supplier_name ?? "Supplier not read"}
        </Link>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[13px] text-muted-foreground">
          <span className="font-mono tabular">{item.invoice_number ?? "no number"}</span>
          <span aria-hidden>·</span>
          <span>{formatDate(item.invoice_date)}</span>
          {item.supplier_id && (
            <Link
              href={queueHref({ ...params, supplier: item.supplier_id }, { resetPage: true })}
              className="relative z-10 rounded-sm underline decoration-dotted underline-offset-4 hover:text-foreground"
            >
              more from this supplier
            </Link>
          )}
        </p>
      </div>

      <div className="min-w-0 text-sm">
        {top ? (
          <>
            <p className="truncate font-medium">{EXCEPTION_LABELS[top.code].title}</p>
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-muted-foreground">
              {more > 0 ? `+${more} more` : EXCEPTION_LABELS[top.code].meaning}
              {item.info_requested && (
                <Badge tone="info" className="relative z-10">
                  <MessageCircleQuestion className="size-3" aria-hidden />
                  Info requested
                </Badge>
              )}
            </p>
          </>
        ) : (
          <p className="text-muted-foreground">No open exceptions</p>
        )}
      </div>

      <div className="flex items-baseline justify-between gap-3 md:block md:text-right">
        <span className="text-[11px] uppercase tracking-wider text-muted-foreground md:hidden">Total</span>
        <Money minor={item.total_minor} currency={item.currency} className="text-[15px] font-medium" />
      </div>

      <div className="flex items-baseline justify-between gap-3 md:block md:text-right">
        <span className="text-[11px] uppercase tracking-wider text-muted-foreground md:hidden">Waiting</span>
        <span className="font-mono tabular text-[13px] text-muted-foreground" title={item.created_at}>
          {formatAge(item.created_at)}
        </span>
      </div>

      <ChevronRight className="hidden size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 md:block" aria-hidden />
    </li>
  );
}

export function QueueList({ items, params }: { items: QueueItem[]; params: QueueParams }) {
  return (
    <div className="overflow-hidden rounded-lg border bg-card shadow-xs">
      <div
        aria-hidden
        className="ledger-rule hidden grid-cols-[6.5rem_minmax(0,1.3fr)_minmax(0,1fr)_9rem_4.5rem_1.25rem] gap-x-6 bg-muted/50 px-5 py-2.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground md:grid"
      >
        <span>Severity</span>
        <span>Supplier</span>
        <span>Top exception</span>
        <span className="text-right">Total</span>
        <span className="text-right">Waiting</span>
        <span />
      </div>
      <ul>
        {items.map((item, i) => (
          <Row key={item.id} item={item} index={i} params={params} />
        ))}
      </ul>
    </div>
  );
}
