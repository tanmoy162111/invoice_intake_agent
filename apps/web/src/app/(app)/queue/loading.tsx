import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div className="flex flex-col gap-6" role="status" aria-label="Loading the queue">
      <div className="flex flex-col gap-3">
        <Skeleton className="h-11 w-72" />
        <Skeleton className="h-4 w-96 max-w-full" />
      </div>
      <Skeleton className="h-12 w-full max-w-md" />
      <div className="overflow-hidden rounded-lg border bg-card">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex items-center gap-6 border-b px-5 py-4 last:border-b-0">
            <Skeleton className="h-6 w-16" />
            <div className="flex flex-1 flex-col gap-2">
              <Skeleton className="h-5 w-56 max-w-full" />
              <Skeleton className="h-3.5 w-40" />
            </div>
            <Skeleton className="hidden h-5 w-24 md:block" />
          </div>
        ))}
      </div>
    </div>
  );
}
