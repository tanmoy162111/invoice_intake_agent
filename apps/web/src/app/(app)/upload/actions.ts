"use server";

import { ApiError, uploadDocument } from "@/lib/api/server";
import type { UploadOut } from "@/lib/api/types";

export type UploadState =
  | { ok: true; result: UploadOut }
  | { ok: false; error: { code: string; message: string; fix?: string } }
  | undefined;

const MAX_BYTES = 15 * 1024 * 1024;

function known(detail: unknown): { code: string; message: string; fix?: string } | null {
  if (detail && typeof detail === "object" && "code" in detail && "message" in detail) {
    const d = detail as { code: unknown; message: unknown; fix?: unknown };
    if (typeof d.code === "string" && typeof d.message === "string") {
      return { code: d.code, message: d.message, ...(typeof d.fix === "string" ? { fix: d.fix } : {}) };
    }
  }
  return null;
}

export async function uploadAction(_previous: UploadState, form: FormData): Promise<UploadState> {
  const file = form.get("file");
  if (!(file instanceof File) || file.size === 0) {
    return { ok: false, error: { code: "NO_FILE", message: "Choose a file to upload." } };
  }
  if (file.size > MAX_BYTES) {
    return {
      ok: false,
      error: { code: "FILE_TOO_LARGE", message: "That file is over 15 MB.", fix: "Compress or split it, or scan at a lower resolution." },
    };
  }
  try {
    return { ok: true, result: await uploadDocument(file) };
  } catch (error) {
    if (error instanceof ApiError) {
      const detail = known(error.detail);
      if (detail) return { ok: false, error: detail };
      if (error.status === 411 || error.status === 413) {
        return { ok: false, error: { code: "FILE_TOO_LARGE", message: "That file is too large to upload." } };
      }
    }
    return { ok: false, error: { code: "UPLOAD_FAILED", message: "The upload did not go through. Try again in a moment." } };
  }
}
