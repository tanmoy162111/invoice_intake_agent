import Link from "next/link";

import { queueHref, type QueueParams, type QueueStatus } from "@/lib/queue-params";
import { cn } from "@/lib/utils";

const TABS: { status: QueueStatus; label: string }[] = [
  { status: "needs_review", label: "Needs review" },
  { status: "failed", label: "Could not read" },
  { status: "cleared", label: "Cleared" },
];

export function QueueTabs({ params, totals }: { params: QueueParams; totals: Record<QueueStatus, number> }) {
  return (
    <nav aria-label="Queue" className="flex gap-1 overflow-x-auto border-b scroll-thin">
      {TABS.map(({ status, label }) => {
        const active = params.status === status;
        return (
          <Link
            key={status}
            href={queueHref({ ...params, status, page: 1 })}
            aria-current={active ? "page" : undefined}
            className={cn(
              "-mb-px inline-flex h-12 shrink-0 items-center gap-2.5 border-b-2 px-4 text-sm font-medium transition-colors",
              active
                ? "border-brand text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {label}
            <span
              className={cn(
                "font-mono tabular rounded-sm px-1.5 py-0.5 text-xs",
                active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
              )}
            >
              {totals[status]}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
