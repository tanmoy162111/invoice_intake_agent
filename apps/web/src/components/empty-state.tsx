import { cn } from "@/lib/utils";

export function EmptyState({
  title,
  children,
  stamp,
  className,
}: {
  title: string;
  children?: React.ReactNode;
  stamp?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center gap-3 rounded-lg border border-dashed px-6 py-16 text-center", className)}>
      {stamp && (
        <span
          aria-hidden
          className="stamp-in -rotate-6 rounded-[4px] border-[3px] border-ok px-4 py-1 text-lg font-bold uppercase tracking-[0.25em] text-ok opacity-80"
        >
          {stamp}
        </span>
      )}
      <h2 className="display text-2xl font-medium">{title}</h2>
      {children && <p className="max-w-md text-sm text-muted-foreground">{children}</p>}
    </div>
  );
}
