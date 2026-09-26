"use client";

import { useRouter } from "next/navigation";

import { EXCEPTION_CODES, EXCEPTION_LABELS, type ExceptionCode } from "@/lib/labels";
import { queueHref, type QueueParams } from "@/lib/queue-params";

/** A plain select (it works everywhere, including phones) that changes the address, so a filtered
 * queue can be bookmarked and shared. */
export function ExceptionFilter({ params }: { params: QueueParams }) {
  const router = useRouter();
  return (
    <label className="flex flex-col gap-1.5 text-[13px] font-medium sm:flex-row sm:items-center sm:gap-2">
      <span className="text-muted-foreground">Exception</span>
      <select
        value={params.code ?? ""}
        onChange={(e) => {
          const code = (e.target.value || null) as ExceptionCode | null;
          router.push(queueHref({ ...params, code }, { resetPage: true }));
        }}
        className="h-10 min-w-56 rounded-md border border-input bg-card px-3 text-sm shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <option value="">Any exception</option>
        {EXCEPTION_CODES.map((code) => (
          <option key={code} value={code}>
            {EXCEPTION_LABELS[code].title}
          </option>
        ))}
      </select>
    </label>
  );
}
