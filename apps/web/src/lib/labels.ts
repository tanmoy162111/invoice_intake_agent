import type { components } from "./api/schema";

export type ExceptionCode = components["schemas"]["ExceptionCode"];
export type InvoiceStatus = components["schemas"]["InvoiceStatus"];
export type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

/** Plain-words names for the exception codes. The API's own explanation carries the numbers. */
export const EXCEPTION_LABELS: Record<ExceptionCode, { title: string; meaning: string }> = {
  UNREADABLE_DOCUMENT: { title: "Unreadable document", meaning: "The file could not be read clearly." },
  LOW_CONFIDENCE_FIELD: { title: "Field not certain", meaning: "An important value could not be confirmed." },
  LINE_MATH_MISMATCH: { title: "Line arithmetic", meaning: "A line's quantity times price is not its amount." },
  TOTAL_MISMATCH: { title: "Totals do not add up", meaning: "The lines, tax and total disagree." },
  TAX_MISMATCH: { title: "Tax looks wrong", meaning: "The tax is not the expected share of the subtotal." },
  INVALID_DATE: { title: "Date problem", meaning: "A date is impossible or out of range." },
  UNKNOWN_SUPPLIER: { title: "Unknown supplier", meaning: "The supplier is not in the records." },
  BANK_DETAILS_CHANGED: { title: "Bank details changed", meaning: "The bank account differs from the one on file." },
  POSSIBLE_DUPLICATE: { title: "Possible duplicate", meaning: "This looks like an invoice already received." },
  NO_PO: { title: "No purchase order", meaning: "No PO number, and no open PO fits." },
  PO_NOT_FOUND: { title: "Purchase order not found", meaning: "The PO number is not usable." },
  PRICE_VARIANCE: { title: "Price above the PO", meaning: "A unit price differs from the purchase order." },
  QTY_VARIANCE: { title: "Quantity above the PO", meaning: "More units are billed than ordered." },
  RECEIPT_MISSING: { title: "Nothing received", meaning: "No goods receipt is recorded for the PO." },
  QTY_NOT_RECEIVED: { title: "Billed but not received", meaning: "More is billed than has been received." },
  PO_OVERBILLED: { title: "Purchase order over-billed", meaning: "Invoices now exceed the PO total." },
  CURRENCY_MISMATCH: { title: "Currency mismatch", meaning: "The currency differs from what is expected." },
  ABOVE_APPROVAL_LIMIT: { title: "Above approval limit", meaning: "The total needs a manager's approval." },
};

export const EXCEPTION_CODES = Object.keys(EXCEPTION_LABELS) as ExceptionCode[];

export const STATUS_LABELS: Record<InvoiceStatus, { label: string; tone: Tone }> = {
  received: { label: "Received", tone: "neutral" },
  extracting: { label: "Reading", tone: "info" },
  extracted: { label: "Read", tone: "info" },
  checking: { label: "Checking", tone: "info" },
  cleared: { label: "Cleared", tone: "ok" },
  needs_review: { label: "Needs review", tone: "warn" },
  approved: { label: "Approved", tone: "ok" },
  rejected: { label: "Rejected", tone: "bad" },
  exported: { label: "Exported", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
};

/** Why an invoice was sent to a person, in words. */
export const ROUTING_REASON_LABELS: Record<string, string> = {
  BANK_DETAILS_CHANGED: "Bank account changed",
  OPEN_EXCEPTIONS: "Open exceptions",
  LOW_CONFIDENCE: "A key field is not certain",
  NO_TOTAL: "No total read",
  CREDIT_NOTE: "Negative amount (credit note)",
  ZERO_TOTAL: "Zero total",
  NO_LIMIT: "No approval limit for this currency",
  ABOVE_LIMIT: "Above the approval limit",
  MISSING_CHECKS: "A check did not run",
};

export const CHECK_LABELS: Record<string, string> = {
  LINE_MATH_MISMATCH: "Line arithmetic",
  TOTAL_MISMATCH: "Totals",
  TAX_MISMATCH: "Tax",
  INVALID_DATE: "Dates",
  CURRENCY_MISMATCH: "Currency",
  UNKNOWN_SUPPLIER: "Supplier known",
  BANK_DETAILS_CHANGED: "Bank account",
  POSSIBLE_DUPLICATE: "Duplicate",
  NO_PO: "Purchase order named",
  PO_NOT_FOUND: "Purchase order found",
  PRICE_VARIANCE: "Prices match the PO",
  QTY_VARIANCE: "Quantities match the PO",
  RECEIPT_MISSING: "Goods received",
  QTY_NOT_RECEIVED: "Received quantity covers billing",
  PO_OVERBILLED: "Within the PO total",
};

export const FIELD_LABELS: Record<string, string> = {
  supplier_name: "Supplier",
  supplier_tax_id: "Tax ID",
  supplier_address: "Address",
  supplier_bank_account: "Bank account",
  invoice_number: "Invoice number",
  invoice_date: "Invoice date",
  due_date: "Due date",
  po_number: "PO number",
  currency: "Currency",
  subtotal: "Subtotal",
  tax_total: "Tax",
  total: "Total",
  payment_terms: "Payment terms",
};

/** The fields that decide where an invoice goes: if one is doubtful, a person must look. */
export const KEY_FIELDS: readonly string[] = ["supplier_name", "invoice_number", "invoice_date", "total", "currency"];

export function isKeyField(name: string): boolean {
  return KEY_FIELDS.includes(name);
}

export function fieldLabel(name: string): string {
  const known = FIELD_LABELS[name];
  if (known) return known;
  const words = name.replace(/[._]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
