import { Check, Minus, X } from "lucide-react";

import type { InvoiceDetail } from "@/lib/api/types";
import { CHECK_LABELS } from "@/lib/labels";
import { cn } from "@/lib/utils";

const OUTCOME = {
  pass: { icon: Check, text: "Passed", tone: "text-ok" },
  fail: { icon: X, text: "Failed", tone: "text-block" },
  skipped: { icon: Minus, text: "Could not be checked", tone: "text-review" },
} as const;

export function ChecksPanel({ detail }: { detail: InvoiceDetail }) {
  if (detail.checks.length === 0) {
    return <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">No checks have run yet.</p>;
  }
  return (
    <ul className="divide-y overflow-hidden rounded-lg border bg-card">
      {detail.checks.map((c) => {
        const o = OUTCOME[c.outcome as keyof typeof OUTCOME] ?? OUTCOME.skipped;
        const Icon = o.icon;
        return (
          <li key={c.code} className="flex items-center justify-between gap-4 px-4 py-3 text-sm">
            <span>{CHECK_LABELS[c.code] ?? c.code}</span>
            <span className={cn("inline-flex items-center gap-1.5 font-medium", o.tone)}>
              <Icon className="size-4" aria-hidden /> {o.text}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
