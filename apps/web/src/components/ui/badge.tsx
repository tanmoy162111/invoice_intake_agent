import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

/** Tones share one palette with the severity stamps: ink on a tinted ground. */
const badgeVariants = cva(
  "inline-flex items-center gap-1 whitespace-nowrap rounded-sm border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[0.08em]",
  {
    variants: {
      tone: {
        neutral: "border-border bg-muted text-muted-foreground",
        ok: "border-ok/30 bg-ok-soft text-ok",
        warn: "border-review/30 bg-review-soft text-review",
        bad: "border-block/30 bg-block-soft text-block",
        info: "border-info/30 bg-info-soft text-info",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

function Badge({
  className,
  tone,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

export { Badge, badgeVariants };
