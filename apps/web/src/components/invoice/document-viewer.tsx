"use client";

import { ChevronLeft, ChevronRight, Minus, Plus, ScanSearch } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const ZOOMS = [1, 1.5, 2, 3] as const;

/** One page image. It is remounted (by its key) for every page, so each starts as "loading". */
function PageImage({ src, alt, zoom }: { src: string; alt: string; zoom: number }) {
  const [loaded, setLoaded] = React.useState(false);
  return (
    <>
      {!loaded && <Skeleton className="absolute inset-4 aspect-[1/1.35] bg-card/60" />}
      {/* eslint-disable-next-line @next/next/no-img-element -- a private, per-session image: not for the image optimizer */}
      <img
        src={src}
        alt={alt}
        onLoad={() => setLoaded(true)}
        draggable={false}
        className={cn("mx-auto h-auto max-w-none rounded-[2px] bg-white shadow-page transition-opacity", loaded ? "opacity-100" : "opacity-0")}
        style={{ width: `${zoom * 100}%` }}
      />
    </>
  );
}

/** The document as the reader saw it: page images on a desk, with zoom and page keys. */
export function DocumentViewer({
  invoiceId,
  pageCount,
  page,
  onPage,
  filename,
  className,
}: {
  invoiceId: string;
  pageCount: number | null;
  page: number;
  onPage: (page: number) => void;
  filename: string;
  className?: string;
}) {
  const [zoom, setZoom] = React.useState<number>(1);
  const pages = pageCount && pageCount > 0 ? pageCount : 0;
  const current = Math.min(Math.max(page, 1), Math.max(pages, 1));
  const src = `/api/pages/${invoiceId}/${current}`;

  function go(delta: number) {
    onPage(Math.min(Math.max(current + delta, 1), pages));
  }
  function step(delta: number) {
    const i = ZOOMS.indexOf(zoom as (typeof ZOOMS)[number]);
    setZoom(ZOOMS[Math.min(Math.max(i + delta, 0), ZOOMS.length - 1)] ?? 1);
  }

  if (pages === 0) {
    return (
      <div className={cn("grid min-h-64 place-items-center rounded-lg bg-desk p-8 text-center", className)}>
        <div className="flex flex-col items-center gap-2 text-muted-foreground">
          <ScanSearch className="size-8" aria-hidden />
          <p className="text-sm">The document pages are not available yet.</p>
        </div>
      </div>
    );
  }

  return (
    <section
      aria-label="Document"
      className={cn("flex flex-col overflow-hidden rounded-lg border bg-desk", className)}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight" || e.key === "PageDown") go(1);
        if (e.key === "ArrowLeft" || e.key === "PageUp") go(-1);
      }}
    >
      <div className="flex items-center justify-between gap-2 border-b bg-card/80 px-3 py-2 backdrop-blur">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" className="size-9" onClick={() => go(-1)} disabled={current <= 1} aria-label="Previous page">
            <ChevronLeft />
          </Button>
          <span className="min-w-20 text-center text-sm tabular" aria-live="polite">
            Page <span className="font-mono">{current}</span> of <span className="font-mono">{pages}</span>
          </span>
          <Button variant="ghost" size="icon" className="size-9" onClick={() => go(1)} disabled={current >= pages} aria-label="Next page">
            <ChevronRight />
          </Button>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" className="size-9" onClick={() => step(-1)} disabled={zoom <= 1} aria-label="Zoom out">
            <Minus />
          </Button>
          <span className="w-12 text-center font-mono text-xs tabular text-muted-foreground">{Math.round(zoom * 100)}%</span>
          <Button variant="ghost" size="icon" className="size-9" onClick={() => step(1)} disabled={zoom >= 3} aria-label="Zoom in">
            <Plus />
          </Button>
        </div>
      </div>

      <div className="relative max-h-[calc(100dvh-12rem)] min-h-72 flex-1 overflow-auto p-4 scroll-thin" tabIndex={0} aria-label={`Page ${current} of ${filename}`}>
        <PageImage key={src} src={src} alt={`Page ${current} of ${pages} of ${filename}`} zoom={zoom} />
      </div>

      {pages > 1 && (
        <div className="flex gap-2 overflow-x-auto border-t bg-card/80 p-2 scroll-thin" role="tablist" aria-label="Pages">
          {Array.from({ length: pages }, (_, i) => i + 1).map((n) => (
            <button
              key={n}
              role="tab"
              aria-selected={n === current}
              onClick={() => onPage(n)}
              className={cn(
                "h-9 min-w-9 shrink-0 rounded-md border px-2 font-mono text-xs tabular transition-colors",
                n === current ? "border-brand bg-brand/10 text-foreground" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {n}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
