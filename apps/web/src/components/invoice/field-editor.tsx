"use client";

import { Eye, EyeOff, LoaderCircle, Pencil } from "lucide-react";
import * as React from "react";
import { useActionState } from "react";

import { correctAction, revealBankAction, type RevealState } from "@/app/(app)/invoices/[id]/actions";
import { FormMessage } from "@/components/invoice/form-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fieldLabel } from "@/lib/labels";

/** Correct one header field. The API re-runs the checks, and the page refreshes with the new result. */
export function FieldEditor({ invoiceId, field, current }: { invoiceId: string; field: string; current: string }) {
  const [open, setOpen] = React.useState(false);
  const [state, run, pending] = useActionState(correctAction.bind(null, invoiceId), undefined);
  const [seen, setSeen] = React.useState(state);
  if (state !== seen) {
    // A saved correction closes the box (state adjusted while rendering, not in an effect).
    setSeen(state);
    if (state?.ok) setOpen(false);
  }
  const label = fieldLabel(field);
  if (!open) {
    return (
      <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(true)} aria-label={`Correct ${label}`}>
        <Pencil /> Correct
      </Button>
    );
  }
  return (
    <form action={run} className="flex w-full flex-col gap-2 sm:col-span-3">
      <input type="hidden" name="field" value={field} />
      <label htmlFor={`fix-${field}`} className="sr-only">
        New value for {label}
      </label>
      <Input id={`fix-${field}`} name="value" defaultValue={current} autoFocus spellCheck={false} />
      <FormMessage state={state} />
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={pending}>
          {pending && <LoaderCircle className="animate-spin" />} Save and re-check
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** The bank account stays masked until someone asks; the API writes each reveal to the history. */
export function BankReveal({ invoiceId }: { invoiceId: string }) {
  const [state, setState] = React.useState<RevealState>(undefined);
  const [pending, start] = React.useTransition();
  if (state?.ok) {
    return (
      <span className="inline-flex items-center gap-2">
        <span className="font-mono tabular" data-testid="bank-revealed">{state.account}</span>
        <Button type="button" variant="ghost" size="sm" onClick={() => setState(undefined)}>
          <EyeOff /> Hide
        </Button>
      </span>
    );
  }
  return (
    <span className="inline-flex flex-col items-start gap-1">
      <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => start(async () => setState(await revealBankAction(invoiceId)))}>
        {pending ? <LoaderCircle className="animate-spin" /> : <Eye />} Reveal
      </Button>
      {state && !state.ok && <span role="alert" className="text-xs text-block">{state.message}</span>}
    </span>
  );
}
