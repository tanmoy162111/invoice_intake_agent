import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

/** How sure the reader was: a bar plus the number (colour is never the only signal). */
export function ConfidenceBar({ value, minimum = 0.8 }: { value: number | null; minimum?: number }) {
  if (value === null) {
    return <span className="text-xs text-muted-foreground">not read</span>;
  }
  const tone = value >= minimum ? "bg-ok" : value >= 0.5 ? "bg-review" : "bg-block";
  return (
    <span className="inline-flex items-center gap-2" role="img" aria-label={`Confidence ${formatPercent(value)}`}>
      <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <span className={cn("absolute inset-y-0 left-0 rounded-full", tone)} style={{ width: `${Math.round(value * 100)}%` }} />
        <span
          aria-hidden
          className="absolute inset-y-[-2px] w-px bg-foreground/40"
          style={{ left: `${Math.round(minimum * 100)}%` }}
        />
      </span>
      <span className="font-mono tabular text-xs text-muted-foreground">{formatPercent(value)}</span>
    </span>
  );
}
