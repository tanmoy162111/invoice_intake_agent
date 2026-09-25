import { Badge } from "@/components/ui/badge";
import { STATUS_LABELS, type InvoiceStatus } from "@/lib/labels";

export function StatusPill({ status }: { status: InvoiceStatus }) {
  const s = STATUS_LABELS[status];
  return <Badge tone={s.tone}>{s.label}</Badge>;
}
