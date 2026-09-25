import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { InvoiceHeader } from "@/components/invoice/invoice-header";
import { InvoiceWorkspace } from "@/components/invoice/invoice-workspace";
import { getInvoice } from "@/lib/api/server";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  return { title: UUID.test(id) ? "Invoice" : "Not found" };
}

export default async function InvoicePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!UUID.test(id)) notFound();
  const detail = await getInvoice(id);
  return (
    <div className="flex flex-col gap-8">
      <InvoiceHeader detail={detail} />
      <InvoiceWorkspace detail={detail} />
    </div>
  );
}
