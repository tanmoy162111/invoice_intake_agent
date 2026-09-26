import { cn } from "@/lib/utils";
import { formatMoney } from "@/lib/format";

/** An amount. Small amounts are set in the mono face; a large headline amount in the serif, where a
 * mono decimal point would look gappy. */
export function Money({
  minor,
  currency,
  className,
  display = false,
}: {
  minor: number | null | undefined;
  currency: string | null | undefined;
  className?: string;
  display?: boolean;
}) {
  return <span className={cn(display ? "display tabular" : "font-mono tabular", className)}>{formatMoney(minor, currency)}</span>;
}
