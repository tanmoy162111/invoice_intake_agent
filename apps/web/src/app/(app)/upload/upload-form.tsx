"use client";

import { CheckCircle2, FileUp, LoaderCircle, TriangleAlert } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { useActionState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { uploadAction, type UploadState } from "./actions";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,application/pdf,image/png,image/jpeg,image/tiff";

function size(bytes: number): string {
  return bytes > 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB`;
}

export function UploadForm() {
  const [state, action, pending] = useActionState<UploadState, FormData>(uploadAction, undefined);
  const input = React.useRef<HTMLInputElement>(null);
  const [file, setFile] = React.useState<File | null>(null);
  const [over, setOver] = React.useState(false);

  function choose(files: FileList | null) {
    setFile(files?.[0] ?? null);
  }

  return (
    <div className="flex flex-col gap-6">
      <form action={action} className="flex flex-col gap-4">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            if (input.current && e.dataTransfer.files.length > 0) {
              input.current.files = e.dataTransfer.files;
              choose(e.dataTransfer.files);
            }
          }}
          className={cn(
            "flex flex-col items-center gap-4 rounded-xl border-2 border-dashed bg-card px-6 py-14 text-center transition-colors",
            over ? "border-brand bg-brand/5" : "border-input",
          )}
        >
          <span aria-hidden className="grid size-14 place-items-center rounded-full bg-muted text-muted-foreground">
            <FileUp className="size-6" />
          </span>
          <div>
            <p className="display text-2xl font-medium">Drop an invoice here</p>
            <p className="mt-1 text-sm text-muted-foreground">PDF, PNG, JPEG or TIFF · up to 15 MB and 10 pages</p>
          </div>
          <input
            ref={input}
            id="file"
            name="file"
            type="file"
            accept={ACCEPT}
            className="sr-only"
            onChange={(e) => choose(e.target.files)}
          />
          <label htmlFor="file" className={cn(buttonVariants({ variant: "outline" }), "cursor-pointer")}>
            Choose a file
          </label>
          {file && (
            <p className="rounded-md bg-muted px-3 py-1.5 font-mono text-[13px] tabular" data-testid="chosen-file">
              {file.name} · {size(file.size)}
            </p>
          )}
        </div>
        <Button type="submit" size="lg" disabled={pending || !file}>
          {pending ? <LoaderCircle className="animate-spin" /> : <FileUp />}
          {pending ? "Uploading…" : "Upload and check"}
        </Button>
      </form>

      {state && !state.ok && (
        <div role="alert" className="flex gap-3 rounded-lg border border-block/30 bg-block-soft px-4 py-3.5 text-block">
          <TriangleAlert className="mt-0.5 size-5 shrink-0" aria-hidden />
          <div>
            <p className="font-medium">{state.error.message}</p>
            {state.error.fix && <p className="mt-0.5 text-sm opacity-90">{state.error.fix}</p>}
          </div>
        </div>
      )}

      {state?.ok && (
        <div role="status" className="flex flex-col gap-3 rounded-lg border border-ok/30 bg-ok-soft px-4 py-4 text-ok" data-testid="upload-result">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="mt-0.5 size-5 shrink-0" aria-hidden />
            <div>
              <p className="font-medium">
                {state.result.duplicate ? "That file was already uploaded." : "Uploaded. It is being read and checked."}
              </p>
              <p className="mt-0.5 text-sm opacity-90">
                Reading and checking take about a minute; the invoice then appears in the queue if it needs a person.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-foreground">
            <Badge>{state.result.filename}</Badge>
            <Badge>{state.result.doc_quality} document</Badge>
            {state.result.page_count ? <Badge>{state.result.page_count} {state.result.page_count === 1 ? "page" : "pages"}</Badge> : null}
            <Link href={`/invoices/${state.result.invoice_id}`} className={cn(buttonVariants({ size: "sm" }), "ml-auto")}>
              Open the invoice
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
