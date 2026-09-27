"use server";

import { revalidatePath } from "next/cache";

import {
  ApiError,
  approveInvoice,
  closeException,
  correctField,
  rejectInvoice,
  requestInfo,
  revealBank,
} from "@/lib/api/server";
import {
  UUID,
  cleanNote,
  closeProblem,
  isCorrectable,
  optionalNoteProblem,
  rejectProblem,
  type InputProblem,
  type Severity,
} from "@/lib/review-input";

/** What a form shows after it ran. `ok: true` means the page has already been refreshed. */
export type ActionState = { ok: true } | { ok: false; message: string } | undefined;

function text(form: FormData, name: string): string {
  const v = form.get(name);
  return typeof v === "string" ? v : "";
}

function refuse(problem: InputProblem): ActionState {
  return { ok: false, message: problem.message };
}

function message(error: unknown): string {
  if (error instanceof ApiError) {
    const d = error.detail;
    if (d && typeof d === "object" && "message" in d && typeof d.message === "string") return d.message;
    if (error.status === 404) return "That invoice or exception no longer exists.";
  }
  return "That did not go through. Try again in a moment.";
}

async function run(id: string, work: () => Promise<unknown>): Promise<ActionState> {
  if (!UUID.test(id)) return { ok: false, message: "That invoice could not be found." };
  try {
    await work();
  } catch (error) {
    // Not-found and login redirects are thrown by the API helpers and must pass through.
    if (!(error instanceof ApiError)) throw error;
    return { ok: false, message: message(error) };
  }
  revalidatePath(`/invoices/${id}`);
  revalidatePath("/queue");
  return { ok: true };
}

export async function approveAction(id: string, _previous: ActionState, form: FormData): Promise<ActionState> {
  const note = text(form, "note");
  const problem = optionalNoteProblem(note);
  if (problem) return refuse(problem);
  return run(id, () => approveInvoice(id, cleanNote(note)));
}

export async function rejectAction(id: string, _previous: ActionState, form: FormData): Promise<ActionState> {
  const reason = text(form, "reason");
  const problem = rejectProblem(reason);
  if (problem) return refuse(problem);
  return run(id, () => rejectInvoice(id, cleanNote(reason) ?? ""));
}

export async function requestInfoAction(id: string, _previous: ActionState, form: FormData): Promise<ActionState> {
  const note = text(form, "note");
  const problem = optionalNoteProblem(note);
  if (problem) return refuse(problem);
  return run(id, () => requestInfo(id, cleanNote(note)));
}

export async function correctAction(id: string, _previous: ActionState, form: FormData): Promise<ActionState> {
  const field = text(form, "field");
  if (!isCorrectable(field)) return { ok: false, message: "That field cannot be corrected." };
  return run(id, () => correctField(id, field, text(form, "value")));
}

export async function closeExceptionAction(
  id: string,
  exceptionId: string,
  severity: Severity,
  _previous: ActionState,
  form: FormData,
): Promise<ActionState> {
  const resolution = text(form, "resolution");
  if (resolution !== "resolved" && resolution !== "dismissed") return { ok: false, message: "Choose resolve or dismiss." };
  if (!UUID.test(exceptionId)) return { ok: false, message: "That exception could not be found." };
  const note = text(form, "note");
  const problem = closeProblem(severity, note);
  if (problem) return refuse(problem);
  return run(id, () => closeException(exceptionId, resolution, cleanNote(note)));
}

export type RevealState = { ok: true; account: string } | { ok: false; message: string } | undefined;

/** Reads the account once. It is returned to the page and not stored; the API records that it was shown. */
export async function revealBankAction(id: string): Promise<RevealState> {
  if (!UUID.test(id)) return { ok: false, message: "That invoice could not be found." };
  try {
    return { ok: true, account: await revealBank(id) };
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    return { ok: false, message: message(error) };
  }
}
