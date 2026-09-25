import type { Metadata } from "next";
import Link from "next/link";
import { X } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ExceptionFilter } from "@/components/queue/exception-filter";
import { Pagination } from "@/components/queue/pagination";
import { QueueList } from "@/components/queue/queue-list";
import { QueueTabs } from "@/components/queue/queue-tabs";
import { getQueue, getQueueTotals } from "@/lib/api/server";
import { parseQueueParams, queueHref, toApiQuery } from "@/lib/queue-params";

export const metadata: Metadata = { title: "Review queue" };

const COPY = {
  needs_review: "Worst problem first, then oldest. Open one to see what is wrong and decide.",
  failed: "Documents that could not be read. Ask the supplier for a clearer copy.",
  cleared: "Nothing is in doubt. These wait for a person to approve them.",
} as const;

export default async function QueuePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = parseQueueParams(await searchParams);
  const [queue, totals] = await Promise.all([getQueue(toApiQuery(params)), getQueueTotals()]);
  const filtered = params.code !== null || params.supplier !== null;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="display text-[2.4rem] font-medium leading-none tracking-tight sm:text-5xl">Review queue</h1>
        <p className="mt-2 max-w-2xl text-[15px] text-muted-foreground">{COPY[params.status]}</p>
      </header>

      <QueueTabs params={params} totals={totals} />

      <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
        <ExceptionFilter params={params} />
        {params.supplier && (
          <Link
            href={queueHref({ ...params, supplier: null }, { resetPage: true })}
            className="inline-flex h-10 items-center gap-2 rounded-md border border-input bg-card px-3 text-sm hover:bg-muted"
          >
            One supplier only <X className="size-3.5" aria-hidden />
            <span className="sr-only">Show all suppliers</span>
          </Link>
        )}
        <p className="ml-auto text-sm text-muted-foreground" aria-live="polite">
          <span className="font-mono tabular text-foreground">{queue.total}</span>{" "}
          {queue.total === 1 ? "invoice" : "invoices"}
          {filtered ? " match" : ""}
        </p>
      </div>

      {queue.items.length === 0 ? (
        filtered ? (
          <EmptyState title="Nothing matches those filters">
            Try another exception, or clear the supplier filter.
          </EmptyState>
        ) : params.status === "needs_review" ? (
          <EmptyState title="The queue is clear" stamp="All clear">
            Nothing needs a person right now. New invoices appear here as they are read and checked.
          </EmptyState>
        ) : (
          <EmptyState title="Nothing here">No invoices in this list.</EmptyState>
        )
      ) : (
        <QueueList items={queue.items} params={params} />
      )}

      <Pagination params={params} total={queue.total} />
    </div>
  );
}
