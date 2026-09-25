import { CheckCircle2, CircleSlash, Hourglass } from "lucide-react";

import type { InvoiceDetail } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/** Where this invoice stands: what still stands in the way of approving it. */
export function DecisionSummary({ detail }: { detail: InvoiceDetail }) {
  const final = detail.status === "approved" || detail.status === "rejected" || detail.status === "exported";
  const open = detail.status === "needs_review" || detail.status === "cleared";
  let Icon = Hourglass;
  let tone = "border-info/30 bg-info-soft text-info";
  let title = "Being processed";
  let body = "The invoice is still being read and checked. Refresh in a moment.";
  if (final) {
    Icon = detail.status === "rejected" ? CircleSlash : CheckCircle2;
    tone = detail.status === "rejected" ? "border-block/30 bg-block-soft text-block" : "border-ok/30 bg-ok-soft text-ok";
    title = detail.status === "rejected" ? "Rejected" : detail.status === "exported" ? "Approved and exported" : "Approved";
    body = "This decision is final and recorded.";
  } else if (detail.status === "failed") {
    Icon = CircleSlash;
    tone = "border-block/30 bg-block-soft text-block";
    title = "Could not be read";
    body = "Ask the supplier for a clearer copy of the document.";
  } else if (open && detail.can_approve) {
    Icon = CheckCircle2;
    tone = "border-ok/30 bg-ok-soft text-ok";
    title = "Ready to approve";
    body = "Every exception is closed.";
  } else if (open) {
    Icon = Hourglass;
    tone = "border-review/30 bg-review-soft text-review";
    title = `${detail.approval_blockers} open ${detail.approval_blockers === 1 ? "exception" : "exceptions"}`;
    body = "Every exception must be resolved or dismissed before this can be approved.";
  }
  return (
    <div className={cn("flex items-start gap-3 rounded-lg border px-4 py-3.5", tone)}>
      <Icon className="mt-0.5 size-5 shrink-0" aria-hidden />
      <div>
        <p className="display text-lg font-medium leading-tight">{title}</p>
        <p className="mt-0.5 text-sm opacity-90">{body}</p>
      </div>
    </div>
  );
}
