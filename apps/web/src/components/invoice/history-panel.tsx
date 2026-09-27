"use client";

import { Download } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AuditOut } from "@/lib/api/types";
import { formatAge } from "@/lib/format";
import { ACTOR_LABELS } from "@/lib/labels";

/** The invoice's whole history (playbook M9): every status change, check, exception, human action
 * and model call, oldest first. `audit` is already the full JSON export; downloading it is just
 * saving the same response, not a separate request. */
export function HistoryPanel({ audit }: { audit: AuditOut }) {
  function download() {
    const blob = new Blob([JSON.stringify(audit, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `invoice-${audit.invoice_id}-audit.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (audit.entries.length === 0) {
    return (
      <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
        Nothing has happened on this invoice yet.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={download}>
          <Download /> Download JSON
        </Button>
      </div>
      <ol className="divide-y overflow-hidden rounded-lg border bg-card">
        {audit.entries.map((e) => {
          const actor = ACTOR_LABELS[e.actor_type] ?? {
            label: e.actor_type,
            tone: "neutral" as const,
          };
          return (
            <li key={e.id} className="flex flex-col gap-1.5 px-4 py-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono tabular text-[13px] text-muted-foreground" title={e.at}>
                  {formatAge(e.at)}
                </span>
                <Badge tone={actor.tone}>
                  {actor.label}
                  {e.actor_id ? ` · ${e.actor_id}` : ""}
                </Badge>
              </div>
              <p>{e.summary}</p>
              <details className="text-xs text-muted-foreground">
                <summary className="cursor-pointer select-none">Raw details</summary>
                <pre className="mt-1.5 overflow-x-auto rounded-sm bg-muted p-2">
                  {JSON.stringify(e.detail, null, 2)}
                </pre>
              </details>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
