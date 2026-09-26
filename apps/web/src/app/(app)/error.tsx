"use client";

import { TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-4 py-24 text-center" role="alert">
      <TriangleAlert className="size-8 text-review" aria-hidden />
      <h1 className="display text-3xl font-medium">Something went wrong</h1>
      <p className="text-sm text-muted-foreground">
        The page could not be loaded. Your work is safe; nothing was changed. Try again in a moment.
      </p>
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
