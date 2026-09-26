import { AlertTriangle, BadgeCheck, FileSearch, Lock } from "lucide-react";

import { ConfidenceBar } from "@/components/invoice/confidence-bar";
import type { FieldOut, InvoiceDetail } from "@/lib/api/types";
import { formatDate, formatMoney } from "@/lib/format";
import { fieldLabel, isKeyField } from "@/lib/labels";
import { cn } from "@/lib/utils";

const MONEY = new Set(["subtotal", "tax_total", "total"]);
const DATES = new Set(["invoice_date", "due_date"]);

function show(f: FieldOut, currency: string | null): React.ReactNode {
  if (f.value === null || f.value === "") return <span className="text-muted-foreground">not read</span>;
  if (f.field === "supplier_bank_account") {
    return (
      <span className="inline-flex items-center gap-1.5 font-mono tabular">
        <Lock className="size-3.5 text-muted-foreground" aria-hidden />
        {f.value}
      </span>
    );
  }
  if (MONEY.has(f.field) && /^-?\d+$/.test(f.value)) return <span className="font-mono tabular">{formatMoney(Number(f.value), currency)}</span>;
  if (DATES.has(f.field)) return formatDate(f.value);
  // Text is shown as printed on the document. The stored value is a lower-cased form used for
  // matching, so it is only shown when a person has corrected the field.
  const text = f.corrected ? f.value : (f.raw ?? f.value);
  if (f.field === "invoice_number" || f.field === "po_number") return <span className="font-mono tabular">{text}</span>;
  return text;
}

function Row({ field, currency, onJump, alert }: { field: FieldOut; currency: string | null; onJump: (page: number) => void; alert: boolean }) {
  return (
    <li className={cn("grid gap-x-4 gap-y-1.5 px-4 py-3.5 sm:grid-cols-[9rem_1fr_auto] sm:items-start", alert && "bg-review-soft/40")}>
      <div className="flex items-center gap-1.5 text-[13px] font-medium text-muted-foreground">
        {alert && <AlertTriangle className="size-3.5 text-review" aria-label="Not certain" />}
        {fieldLabel(field.field)}
      </div>
      <div className="min-w-0">
        <p className="break-words text-[15px]">{show(field, currency)}</p>
        {field.weak && field.reason && (
          <p className={cn("mt-1 text-[13px] leading-snug", alert ? "text-review" : "text-muted-foreground")}>
            {alert ? "Not certain" : "Lower confidence"}: {field.reason}.
          </p>
        )}
        {field.corrected && (
          <p className="mt-1 inline-flex items-center gap-1 text-[13px] text-ok">
            <BadgeCheck className="size-3.5" aria-hidden /> Corrected by {field.corrected_by ?? "a reviewer"}
          </p>
        )}
      </div>
      <div className="flex items-center gap-3 sm:justify-end">
        {!field.corrected && <ConfidenceBar value={field.confidence} />}
        {field.page !== null && (
          <button
            type="button"
            onClick={() => onJump(field.page as number)}
            className="inline-flex h-8 items-center gap-1 rounded-md border bg-card px-2 font-mono text-xs tabular text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label={`Show page ${field.page}`}
          >
            <FileSearch className="size-3.5" aria-hidden /> p.{field.page}
          </button>
        )}
      </div>
    </li>
  );
}

export function FieldsPanel({ detail, onJump }: { detail: InvoiceDetail; onJump: (page: number) => void }) {
  const currency = detail.header.currency;
  const key = detail.fields.filter((f) => isKeyField(f.field));
  const other = detail.fields.filter((f) => !isKeyField(f.field));
  const attention = key.filter((f) => f.weak).length;
  return (
    <div className="flex flex-col gap-5">
      <section aria-labelledby="key-fields">
        <h3
          id="key-fields"
          className={cn(
            "mb-2 text-[11px] font-semibold uppercase tracking-[0.14em]",
            attention > 0 ? "text-review" : "text-muted-foreground",
          )}
        >
          Key fields{attention > 0 ? ` · ${attention} not certain` : ""}
        </h3>
        <ul className="divide-y overflow-hidden rounded-lg border bg-card">
          {key.map((f) => (
            <Row key={f.field} field={f} currency={currency} onJump={onJump} alert={f.weak} />
          ))}
        </ul>
        <p className="mt-2 text-xs text-muted-foreground">
          The supplier, invoice number, date, total and currency decide where an invoice goes. If one is not
          certain, a person must confirm it.
        </p>
      </section>
      {other.length > 0 && (
        <section aria-labelledby="other-fields">
          <h3 id="other-fields" className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            Other fields
          </h3>
          <ul className="divide-y overflow-hidden rounded-lg border bg-card">
            {other.map((f) => (
              <Row key={f.field} field={f} currency={currency} onJump={onJump} alert={false} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
