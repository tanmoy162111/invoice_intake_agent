/** What the review forms check before they call the API. The API checks again; this only saves a round trip. */

export const NOTE_MAX = 2000;

/** The header fields a reviewer may correct: the same list as the API (the bank account is never edited). */
export const CORRECTABLE_FIELDS: readonly string[] = [
  "supplier_name",
  "supplier_tax_id",
  "supplier_address",
  "invoice_number",
  "invoice_date",
  "due_date",
  "po_number",
  "currency",
  "subtotal",
  "tax_total",
  "total",
  "payment_terms",
];

export function isCorrectable(field: string): boolean {
  return CORRECTABLE_FIELDS.includes(field);
}

export type Severity = "block" | "review" | "info";

/** The note as it will be sent: trimmed, or null when empty. */
export function cleanNote(note: string | null | undefined): string | null {
  const text = (note ?? "").trim();
  return text === "" ? null : text;
}

export type InputProblem = { code: "NOTE_REQUIRED" | "NOTE_TOO_LONG"; message: string };

function tooLong(text: string | null): InputProblem | null {
  return text !== null && text.length > NOTE_MAX
    ? { code: "NOTE_TOO_LONG", message: `Keep the note under ${NOTE_MAX} characters.` }
    : null;
}

/** Closing an exception: a `block` exception needs a note, whatever the choice. */
export function closeProblem(severity: Severity, note: string | null | undefined): InputProblem | null {
  const text = cleanNote(note);
  if (text === null && severity === "block") {
    return { code: "NOTE_REQUIRED", message: "Say why: a blocking exception needs a note." };
  }
  return tooLong(text);
}

export function rejectProblem(reason: string | null | undefined): InputProblem | null {
  const text = cleanNote(reason);
  if (text === null) return { code: "NOTE_REQUIRED", message: "Give a reason for rejecting this invoice." };
  return tooLong(text);
}

export function optionalNoteProblem(note: string | null | undefined): InputProblem | null {
  return tooLong(cleanNote(note));
}

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
