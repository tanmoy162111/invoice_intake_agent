import "server-only";

import { notFound, redirect } from "next/navigation";

import type { QueueStatus } from "../queue-params";
import { getSessionToken } from "../session";
import type { ExceptionCode } from "../labels";
import type { InvoiceDetail, LoginOut, QueueOut, UploadOut } from "./types";

const apiUrl = process.env.API_URL ?? "http://localhost:8000";

/** The API refused or failed. `detail` is what it said (a code and message for a known problem). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(`API answered ${status}`);
  }
}

async function readDetail(res: Response): Promise<unknown> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return body.detail ?? body;
  } catch {
    return null;
  }
}

/** Calls the API as the signed-in reviewer. Not signed in, or a session that ended: to the login. */
async function authed(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await getSessionToken();
  if (!token) redirect("/login");
  const res = await fetch(`${apiUrl}${path}`, {
    ...init,
    cache: "no-store",
    headers: { ...init.headers, Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) redirect("/login?expired=1");
  return res;
}

async function json<T>(res: Response): Promise<T> {
  if (res.status === 404) notFound();
  if (!res.ok) throw new ApiError(res.status, await readDetail(res));
  return (await res.json()) as T;
}

export async function login(username: string, password: string, visitor?: string): Promise<LoginOut> {
  const res = await fetch(`${apiUrl}/auth/login`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(visitor ? { "X-Forwarded-For": visitor } : {}) },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new ApiError(res.status, await readDetail(res));
  return (await res.json()) as LoginOut;
}

export type QueueQuery = {
  status: QueueStatus[];
  code?: ExceptionCode;
  supplier_id?: string;
  limit: number;
  offset: number;
};

export async function getQueue(query: QueueQuery): Promise<QueueOut> {
  const q = new URLSearchParams();
  for (const s of query.status) q.append("status", s);
  if (query.code) q.set("code", query.code);
  if (query.supplier_id) q.set("supplier_id", query.supplier_id);
  q.set("limit", String(query.limit));
  q.set("offset", String(query.offset));
  return json<QueueOut>(await authed(`/invoices?${q}`));
}

/** How many invoices are in each queue, for the tabs. One cheap request per status. */
export async function getQueueTotals(): Promise<Record<QueueStatus, number>> {
  const statuses: QueueStatus[] = ["needs_review", "failed", "cleared"];
  const answers = await Promise.all(
    statuses.map((status) => getQueue({ status: [status], limit: 1, offset: 0 })),
  );
  return {
    needs_review: answers[0]?.total ?? 0,
    failed: answers[1]?.total ?? 0,
    cleared: answers[2]?.total ?? 0,
  };
}

export async function getInvoice(id: string): Promise<InvoiceDetail> {
  return json<InvoiceDetail>(await authed(`/invoices/${encodeURIComponent(id)}`));
}

export async function getPageImage(id: string, page: number): Promise<Response> {
  return authed(`/invoices/${encodeURIComponent(id)}/pages/${page}`);
}

export async function uploadDocument(file: File): Promise<UploadOut> {
  const form = new FormData();
  form.append("file", file);
  const res = await authed("/documents", { method: "POST", body: form });
  if (!res.ok) throw new ApiError(res.status, await readDetail(res));
  return (await res.json()) as UploadOut;
}

/** The signed-in reviewer. Also checks the session is still good, so a stale cookie ends up at the login. */
export async function getMe(): Promise<{ user: string | null }> {
  return json<{ user: string | null }>(await authed("/auth/me"));
}
