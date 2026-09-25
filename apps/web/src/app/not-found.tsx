import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main id="main" className="grid min-h-dvh place-items-center px-6 text-center">
      <div className="flex max-w-md flex-col items-center gap-4">
        <span aria-hidden className="stamp-in -rotate-6 rounded-[4px] border-[3px] border-brand px-4 py-1 text-lg font-bold uppercase tracking-[0.25em] text-brand">
          Not found
        </span>
        <h1 className="display text-4xl font-medium">We could not find that page</h1>
        <p className="text-sm text-muted-foreground">The invoice may not exist, or it belongs to someone else.</p>
        <Link href="/queue" className={buttonVariants()}>
          Back to the queue
        </Link>
      </div>
    </main>
  );
}
