import { cn } from "@/lib/utils";
import { formatMoney } from "@/lib/format";

export function Money({
  minor,
  currency,
  className,
}: {
  minor: number | null | undefined;
  currency: string | null | undefined;
  className?: string;
}) {
  return <span className={cn("font-mono tabular", className)}>{formatMoney(minor, currency)}</span>;
}
