"use client";

import { CheckCircle2, CircleAlert, LoaderCircle, Search, Send } from "lucide-react";
import * as React from "react";
import { useActionState } from "react";

import {
  handoffAction,
  reconcileAction,
  resolveHandoffAction,
  type ActionState,
} from "@/app/(app)/invoices/[id]/actions";
import { FormMessage } from "@/components/invoice/form-message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import type { HandoffOut } from "@/lib/api/types";

const DRAFT_ONLY = "Creates draft bills only. A human must review and approve bills in the accounting platform.";

const TONE = {
  not_started: "neutral",
  pending: "neutral",
  in_progress: "neutral",
  succeeded: "ok",
  failed: "bad",
  unknown: "warn",
} as const;

const LABEL = {
  not_started: "Not sent",
  pending: "Waiting",
  in_progress: "Sending",
  succeeded: "Draft bill created",
  failed: "Not sent",
  unknown: "Outcome uncertain",
} as const;

function Run({ label, icon, action, variant }: { label: string; icon: React.ReactNode; action: () => Promise<ActionState>; variant: "brand" | "outline" }) {
  const [state, run, pending] = useActionState(() => action(), undefined);
  return (
    <form action={run} className="flex flex-col gap-2">
      <FormMessage state={state} />
      <div>
        <Button type="submit" variant={variant} disabled={pending}>
          {pending ? <LoaderCircle className="animate-spin" /> : icon}
          {label}
        </Button>
      </div>
    </form>
  );
}

function Resolve({ id }: { id: string }) {
  const [state, run, pending] = useActionState(resolveHandoffAction.bind(null, id), undefined);
  return (
    <form action={run} className="flex flex-col gap-3 rounded-lg border bg-background p-4">
      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">I checked the accounting system</legend>
        <Label className="flex items-center gap-2 font-normal">
          <input type="radio" name="found" value="yes" required /> There is a bill for this invoice
        </Label>
        <Label className="flex items-center gap-2 font-normal">
          <input type="radio" name="found" value="no" required /> There is no bill for this invoice
        </Label>
      </fieldset>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="bill-ref">Bill reference (if there is one)</Label>
        <input id="bill-ref" name="bill_id" maxLength={100} className="h-9 rounded-md border bg-background px-3 text-sm" />
      </div>
      <FormMessage state={state} />
      <div>
        <Button type="submit" variant="outline" disabled={pending}>
          {pending && <LoaderCircle className="animate-spin" />} Record what I found
        </Button>
      </div>
    </form>
  );
}

/** Sending an approved invoice to the accounting system as a DRAFT bill, and what happened. */
export function AccountingHandoff({ id, handoff }: { id: string; handoff: HandoffOut }) {
  const Icon = handoff.status === "succeeded" ? CheckCircle2 : CircleAlert;
  return (
    <Card aria-label="Accounting system" data-testid="handoff">
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>Accounting system</CardTitle>
          <p className="text-xs text-muted-foreground">{DRAFT_ONLY}</p>
        </div>
        <Badge tone={TONE[handoff.status]} data-testid="handoff-status">
          {LABEL[handoff.status]}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="flex items-start gap-2 text-sm" role="status" data-testid="handoff-message">
          <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
          {handoff.message}
        </p>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          {handoff.organisation_name && (
            <>
              <dt className="text-muted-foreground">Organisation</dt>
              <dd>{handoff.organisation_name}</dd>
            </>
          )}
          {handoff.external_bill_id && (
            <>
              <dt className="text-muted-foreground">Bill reference</dt>
              <dd className="break-all font-mono text-xs" data-testid="handoff-bill">
                {handoff.external_bill_id}
              </dd>
            </>
          )}
        </dl>
        {!handoff.connected && (
          <p className="text-sm text-muted-foreground" data-testid="handoff-not-connected">
            No accounting system is connected yet, so nothing can be sent.
          </p>
        )}
        {handoff.can_request && (
          <Run label={handoff.status === "failed" ? "Try again" : "Create draft bill"} icon={<Send />} variant="brand" action={() => handoffAction(id)} />
        )}
        {handoff.can_reconcile && (
          <div className="flex flex-col gap-3">
            <Run label="Look for the bill" icon={<Search />} variant="outline" action={() => reconcileAction(id)} />
            <Resolve id={id} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
