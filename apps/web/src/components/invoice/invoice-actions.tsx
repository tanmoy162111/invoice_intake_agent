"use client";

import { CircleSlash, LoaderCircle, MessageCircleQuestion, ThumbsUp } from "lucide-react";
import * as React from "react";
import { useActionState } from "react";

import {
  approveAction,
  rejectAction,
  requestInfoAction,
  type ActionState,
} from "@/app/(app)/invoices/[id]/actions";
import { FormMessage } from "@/components/invoice/form-message";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { InvoiceDetail } from "@/lib/api/types";

type Panel = "approve" | "reject" | "info" | null;

function Panel({
  id,
  label,
  hint,
  required,
  name,
  action,
  submit,
  tone,
  onDone,
}: {
  id: string;
  label: string;
  hint?: string;
  required?: boolean;
  name: string;
  action: (previous: ActionState, form: FormData) => Promise<ActionState>;
  submit: string;
  tone: "default" | "outline";
  onDone: () => void;
}) {
  const [state, run, pending] = useActionState(action, undefined);
  React.useEffect(() => {
    if (state?.ok) onDone();
  }, [state, onDone]);
  return (
    <form action={run} className="flex flex-col gap-3 rounded-lg border bg-card p-4">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={id}>{label}</Label>
        <Textarea id={id} name={name} required={required} maxLength={2000} aria-describedby={hint ? `${id}-hint` : undefined} />
        {hint && (
          <p id={`${id}-hint`} className="text-xs text-muted-foreground">
            {hint}
          </p>
        )}
      </div>
      <FormMessage state={state} />
      <div>
        <Button type="submit" variant={tone} disabled={pending}>
          {pending && <LoaderCircle className="animate-spin" />}
          {submit}
        </Button>
      </div>
    </form>
  );
}

/** Approve, reject, or ask for information. Only shown while the invoice is still open. */
export function InvoiceActions({ detail }: { detail: InvoiceDetail }) {
  const [panel, setPanel] = React.useState<Panel>(null);
  const [done, setDone] = React.useState<string | null>(null);
  const close = React.useCallback(() => setPanel(null), []);
  const finished = (message: string) => () => {
    setPanel(null);
    setDone(message);
  };
  if (detail.status !== "needs_review" && detail.status !== "cleared") return null;
  const toggle = (p: Exclude<Panel, null>) => {
    setDone(null);
    setPanel((cur) => (cur === p ? null : p));
  };
  return (
    <section aria-label="Decision" className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <Button variant="brand" disabled={!detail.can_approve} onClick={() => toggle("approve")} aria-expanded={panel === "approve"}>
          <ThumbsUp /> Approve
        </Button>
        <Button variant="outline" onClick={() => toggle("info")} aria-expanded={panel === "info"}>
          <MessageCircleQuestion /> Request info
        </Button>
        <Button variant="outline" onClick={() => toggle("reject")} aria-expanded={panel === "reject"}>
          <CircleSlash /> Reject
        </Button>
      </div>
      {!detail.can_approve && (
        <p className="text-xs text-muted-foreground" data-testid="approve-blocked">
          Approving unlocks when every exception is resolved or dismissed.
        </p>
      )}
      {done && (
        <p role="status" className="rounded-md border border-ok/30 bg-ok-soft px-3 py-2 text-sm text-ok" data-testid="action-done">
          {done}
        </p>
      )}
      {panel === "approve" && (
        <Panel id="approve-note" label="Note (optional)" name="note" submit="Confirm approval" tone="default" onDone={close}
          action={approveAction.bind(null, detail.id)} />
      )}
      {panel === "info" && (
        <Panel id="info-note" label="What do you need to know?" name="note" submit="Record the request" tone="default" onDone={finished("Recorded in the history. The invoice stays in the queue; nothing was sent to the supplier.")}
          hint="This is written to the history and does not change the invoice or contact anyone." action={requestInfoAction.bind(null, detail.id)} />
      )}
      {panel === "reject" && (
        <Panel id="reject-reason" label="Reason for rejecting" name="reason" required submit="Confirm rejection" tone="default" onDone={close}
          hint="A rejection is final and recorded." action={rejectAction.bind(null, detail.id)} />
      )}
    </section>
  );
}
