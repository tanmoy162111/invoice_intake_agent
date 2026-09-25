import { EXCEPTION_CODES, type ExceptionCode } from "./labels";

export const PAGE_SIZE = 25;
export const QUEUE_STATUSES = ["needs_review", "failed", "cleared"] as const;
export type QueueStatus = (typeof QUEUE_STATUSES)[number];

export type QueueParams = {
  status: QueueStatus;
  code: ExceptionCode | null;
  supplier: string | null;
  page: number;
};

type Raw = Record<string, string | string[] | undefined>;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function first(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** Filters from the address bar. Anything that is not valid is dropped, never passed on. */
export function parseQueueParams(raw: Raw): QueueParams {
  const status = first(raw.status);
  const code = first(raw.code);
  const supplier = first(raw.supplier);
  const pageText = first(raw.page);
  const page = pageText !== undefined && /^\d{1,4}$/.test(pageText) ? Number(pageText) : 1;
  return {
    status: (QUEUE_STATUSES as readonly string[]).includes(status ?? "") ? (status as QueueStatus) : "needs_review",
    code: (EXCEPTION_CODES as readonly string[]).includes(code ?? "") ? (code as ExceptionCode) : null,
    supplier: supplier && UUID.test(supplier) ? supplier : null,
    page: page >= 1 ? page : 1,
  };
}

/** The query the API's `GET /invoices` takes. */
export function toApiQuery(p: QueueParams): {
  status: QueueStatus[];
  code?: ExceptionCode;
  supplier_id?: string;
  limit: number;
  offset: number;
} {
  return {
    status: [p.status],
    ...(p.code ? { code: p.code } : {}),
    ...(p.supplier ? { supplier_id: p.supplier } : {}),
    limit: PAGE_SIZE,
    offset: (p.page - 1) * PAGE_SIZE,
  };
}

/** A link to the queue with these filters (only what differs from the defaults). */
export function queueHref(p: QueueParams, options: { resetPage?: boolean } = {}): string {
  const q = new URLSearchParams();
  if (p.status !== "needs_review") q.set("status", p.status);
  if (p.code) q.set("code", p.code);
  if (p.supplier) q.set("supplier", p.supplier);
  if (p.page > 1 && !options.resetPage) q.set("page", String(p.page));
  const text = q.toString();
  return text ? `/queue?${text}` : "/queue";
}
