import type { Metadata } from "next";

import { UploadForm } from "./upload-form";

export const metadata: Metadata = { title: "Upload" };

export default function UploadPage() {
  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-8">
      <header>
        <h1 className="display text-[2.4rem] font-medium leading-none tracking-tight sm:text-5xl">Upload an invoice</h1>
        <p className="mt-3 text-[15px] text-muted-foreground">
          The document is stored, read, and checked against the supplier records, the purchase order, and earlier
          invoices. Anything that needs a person lands in the review queue.
        </p>
      </header>
      <UploadForm />
    </div>
  );
}
