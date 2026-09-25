import { CheckCircle2, Lightbulb, MinusCircle } from "lucide-react";

import { DuplicateCompare } from "@/components/invoice/duplicate-compare";
import { SeverityStamp } from "@/components/severity-stamp";
import type { ExceptionOut, InvoiceDetail } from "@/lib/api/types";
import { EXCEPTION_LABELS } from "@/lib/labels";
import { cn } from "@/lib/utils";

const ACCENT = { block: "bg-block", review: "bg-review", info: "bg-info" } as const;

/** One problem: what it is, the numbers, and what to do about it. */
export function ExceptionCard({
  exception,
  invoice,
  index,
}: {
  exception: ExceptionOut;
  invoice: InvoiceDetail;
  index: number;
}) {
  const closed = exception.status !== "open";
  const label = EXCEPTION_LABELS[exception.code];
  return (
    <article
      style={{ "--i": index } as React.CSSProperties}
      className={cn("reveal relative overflow-hidden rounded-lg border bg-card p-5 pl-6 shadow-xs", closed && "opacity-75")}
      aria-labelledby={`exc-${exception.id}`}
    >
      <span aria-hidden className={cn("absolute inset-y-0 left-0 w-1.5", closed ? "bg-border" : ACCENT[exception.severity])} />
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <SeverityStamp severity={exception.severity} animate={!closed} index={index} />
        <h3 id={`exc-${exception.id}`} className="display text-[1.3rem] font-medium leading-tight">
          {label.title}
        </h3>
        {closed && (
          <span className="ml-auto inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            {exception.status === "resolved" ? <CheckCircle2 className="size-4 text-ok" aria-hidden /> : <MinusCircle className="size-4" aria-hidden />}
            {exception.status === "resolved" ? "Resolved" : "Dismissed"}
          </span>
        )}
      </header>

      <p className="mt-3 text-[15px] leading-relaxed">{exception.explanation}</p>

      {!closed && (
        <div className="mt-4 flex gap-3 rounded-md bg-muted/70 px-3.5 py-3 text-sm">
          <Lightbulb className="mt-0.5 size-4 shrink-0 text-review" aria-hidden />
          <p>
            <span className="font-semibold">Suggested fix. </span>
            {exception.suggested_fix}
          </p>
        </div>
      )}

      {closed && (
        <blockquote className="mt-3 border-l-2 pl-3 text-sm text-muted-foreground">
          <p>
            {exception.status === "resolved" ? "Resolved" : "Dismissed"} by{" "}
            <span className="font-medium text-foreground">{exception.resolved_by ?? "the system"}</span>
            {exception.resolution_note ? <>: “{exception.resolution_note}”</> : null}
          </p>
        </blockquote>
      )}

      {exception.related_invoice && <DuplicateCompare current={invoice} other={exception.related_invoice} />}
    </article>
  );
}
