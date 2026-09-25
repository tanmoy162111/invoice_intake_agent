"use client";

import { FileText } from "lucide-react";
import * as React from "react";

import { ChecksPanel } from "@/components/invoice/checks-panel";
import { DecisionSummary } from "@/components/invoice/decision-summary";
import { DocumentViewer } from "@/components/invoice/document-viewer";
import { ExceptionCard } from "@/components/invoice/exception-card";
import { FieldsPanel } from "@/components/invoice/fields-panel";
import { LinesPanel } from "@/components/invoice/lines-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { InvoiceDetail } from "@/lib/api/types";
import { isKeyField } from "@/lib/labels";

/** The document beside the findings. On a phone the document opens from a button instead. */
export function InvoiceWorkspace({ detail }: { detail: InvoiceDetail }) {
  const [page, setPage] = React.useState(1);
  const [sheet, setSheet] = React.useState(false);
  const open = detail.exceptions.filter((e) => e.status === "open").length;
  const weak = detail.fields.filter((f) => f.weak && isKeyField(f.field)).length;
  const [tab, setTab] = React.useState(detail.exceptions.length > 0 ? "exceptions" : "fields");

  function jump(to: number) {
    setPage(to);
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches) setSheet(true);
  }

  const viewer = (className?: string) => (
    <DocumentViewer
      invoiceId={detail.id}
      pageCount={detail.document.page_count}
      page={page}
      onPage={setPage}
      filename={detail.document.filename}
      className={className}
    />
  );

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:items-start">
      <div className="hidden lg:sticky lg:top-24 lg:block">{viewer()}</div>

      <div className="flex min-w-0 flex-col gap-5">
        <DecisionSummary detail={detail} />

        <Sheet open={sheet} onOpenChange={setSheet}>
          <SheetTrigger asChild>
            <Button variant="outline" className="lg:hidden">
              <FileText /> View the document
            </Button>
          </SheetTrigger>
          <SheetContent side="bottom" className="h-[92dvh]">
            <SheetTitle>Document</SheetTitle>
            <SheetDescription>{detail.document.filename}</SheetDescription>
            <div className="min-h-0 flex-1 overflow-auto">{sheet && viewer("h-full")}</div>
          </SheetContent>
        </Sheet>

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList aria-label="Invoice details">
            <TabsTrigger value="exceptions">
              Exceptions
              {open > 0 && <Badge tone="warn" className="px-1.5 font-mono tabular tracking-normal">{open}</Badge>}
            </TabsTrigger>
            <TabsTrigger value="fields">
              Fields
              {weak > 0 && <Badge tone="warn" className="px-1.5 font-mono tabular tracking-normal">{weak}</Badge>}
            </TabsTrigger>
            <TabsTrigger value="lines">Lines</TabsTrigger>
            <TabsTrigger value="checks">Checks</TabsTrigger>
          </TabsList>

          <TabsContent value="exceptions" className="flex flex-col gap-4">
            {detail.exceptions.length === 0 ? (
              <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                No exceptions were raised for this invoice.
              </p>
            ) : (
              detail.exceptions.map((e, i) => <ExceptionCard key={e.id} exception={e} invoice={detail} index={i} />)
            )}
          </TabsContent>
          <TabsContent value="fields">
            <FieldsPanel detail={detail} onJump={jump} />
          </TabsContent>
          <TabsContent value="lines">
            <LinesPanel detail={detail} />
          </TabsContent>
          <TabsContent value="checks">
            <ChecksPanel detail={detail} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
