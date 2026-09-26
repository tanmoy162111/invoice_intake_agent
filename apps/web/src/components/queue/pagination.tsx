import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { PAGE_SIZE, queueHref, type QueueParams } from "@/lib/queue-params";
import { cn } from "@/lib/utils";

export function Pagination({ params, total }: { params: QueueParams; total: number }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (pages <= 1) return null;
  const link = (page: number) => queueHref({ ...params, page });
  return (
    <nav aria-label="Pages" className="mt-6 flex items-center justify-between gap-4">
      <p className="text-sm text-muted-foreground">
        Page <span className="font-mono tabular text-foreground">{params.page}</span> of{" "}
        <span className="font-mono tabular">{pages}</span>
      </p>
      <div className="flex gap-2">
        {params.page > 1 ? (
          <Link className={cn(buttonVariants({ variant: "outline", size: "sm" }))} href={link(params.page - 1)}>
            <ChevronLeft /> Previous
          </Link>
        ) : null}
        {params.page < pages ? (
          <Link className={cn(buttonVariants({ variant: "outline", size: "sm" }))} href={link(params.page + 1)}>
            Next <ChevronRight />
          </Link>
        ) : null}
      </div>
    </nav>
  );
}
