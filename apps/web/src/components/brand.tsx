import { cn } from "@/lib/utils";

/** The wordmark: a small vermilion stamp beside the name. */
export function Brand({ className, inverted = false }: { className?: string; inverted?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <span
        aria-hidden
        className="grid size-7 rotate-[-6deg] place-items-center rounded-[4px] border-2 border-brand text-[13px] font-bold leading-none text-brand"
      >
        <span className="display -mt-px italic">i</span>
      </span>
      <span
        className={cn(
          "display text-[1.35rem] font-medium leading-none tracking-tight",
          inverted ? "text-primary-foreground" : "text-foreground",
        )}
      >
        Invoice Intake
      </span>
    </span>
  );
}
