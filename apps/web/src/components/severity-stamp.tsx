import { cn } from "@/lib/utils";
import type { Severity } from "@/lib/severity";

const STYLE: Record<Severity, string> = {
  block: "border-block text-block bg-block-soft",
  review: "border-review text-review bg-review-soft",
  info: "border-info text-info bg-info-soft",
};
const LABEL: Record<Severity, string> = { block: "Block", review: "Review", info: "Info" };

/** A rubber stamp for how serious an exception is. It is pressed on when the page opens. */
export function SeverityStamp({
  severity,
  className,
  animate = false,
  index = 0,
}: {
  severity: Severity;
  className?: string;
  animate?: boolean;
  index?: number;
}) {
  return (
    <span
      style={{ "--i": index } as React.CSSProperties}
      className={cn(
        "inline-block -rotate-2 rounded-[3px] border-2 px-1.5 py-0.5 text-[10.5px] font-bold uppercase leading-none tracking-[0.16em]",
        STYLE[severity],
        animate && "stamp-in",
        className,
      )}
    >
      <span className="sr-only">Severity: </span>
      {LABEL[severity]}
    </span>
  );
}
