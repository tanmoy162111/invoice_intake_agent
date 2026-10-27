import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { InvoiceHeader } from "@/components/invoice/invoice-header";
import { InvoiceWorkspace } from "@/components/invoice/invoice-workspace";
import { AccountingHandoff } from "@/components/invoice/accounting-handoff";
import { ApiError, getHandoff, getInvoice } from "@/lib/api/server";
import type { HandoffOut } from "@/lib/api/types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  return { title: UUID.test(id) ? "Invoice" : "Not found" };
}

export default async function InvoicePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!UUID.test(id)) notFound();
  const detail = await getInvoice(id);
  // Only an approved invoice can go to the accounting system. A failed lookup hides the panel; it
  // never breaks the review screen.
  let handoff: HandoffOut | null = null;
  if (detail.status === "approved") {
    try {
      handoff = await getHandoff(id);
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
    }
  }
  return (
    <div className="flex flex-col gap-8">
      <InvoiceHeader detail={detail} />
      {handoff && <AccountingHandoff id={id} handoff={handoff} />}
      <InvoiceWorkspace detail={detail} />
    </div>
  );
}
