import type { components } from "./schema";

type S = components["schemas"];

export type InvoiceDetail = S["InvoiceDetail"];
export type QueueItem = S["QueueItem"];
export type QueueOut = S["QueueOut"];
export type ExceptionOut = S["ExceptionOut"];
export type FieldOut = S["FieldOut"];
export type LineOut = S["LineOut"];
export type CheckOut = S["CheckOut"];
export type RelatedInvoice = S["RelatedInvoice"];
export type LoginOut = S["LoginOut"];
export type UploadOut = S["DocumentOut"];
export type ErrorDetail = S["ErrorDetail"];
