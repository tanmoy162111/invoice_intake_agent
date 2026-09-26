import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div className="flex flex-col gap-8" role="status" aria-label="Loading the invoice">
      <div className="flex flex-col gap-4">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-12 w-96 max-w-full" />
        <Skeleton className="h-4 w-48" />
      </div>
      <div className="grid gap-8 lg:grid-cols-2">
        <Skeleton className="hidden aspect-[1/1.3] lg:block" />
        <div className="flex flex-col gap-4">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    </div>
  );
}
