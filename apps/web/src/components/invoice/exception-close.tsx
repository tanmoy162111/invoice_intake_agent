"use client";

import { CheckCircle2, LoaderCircle, MinusCircle } from "lucide-react";
import { useActionState } from "react";

import { closeExceptionAction } from "@/app/(app)/invoices/[id]/actions";
import { FormMessage } from "@/components/invoice/form-message";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { ExceptionOut } from "@/lib/api/types";

/** Resolve (the problem was fixed) or dismiss (it is not a problem). A blocking exception needs a note. */
export function ExceptionClose({ invoiceId, exception }: { invoiceId: string; exception: ExceptionOut }) {
  const [state, run, pending] = useActionState(
    closeExceptionAction.bind(null, invoiceId, exception.id, exception.severity),
    undefined,
  );
  const need = exception.severity === "block";
  const noteId = `note-${exception.id}`;
  return (
    <form action={run} className="mt-4 flex flex-col gap-3 border-t pt-4">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={noteId}>{need ? "Note (required)" : "Note (optional)"}</Label>
        <Textarea id={noteId} name="note" required={need} maxLength={2000} />
      </div>
      <FormMessage state={state} />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" name="resolution" value="resolved" variant="outline" disabled={pending}>
          {pending ? <LoaderCircle className="animate-spin" /> : <CheckCircle2 />} Resolve
        </Button>
        <Button type="submit" name="resolution" value="dismissed" variant="outline" disabled={pending}>
          <MinusCircle /> Dismiss
        </Button>
      </div>
    </form>
  );
}
