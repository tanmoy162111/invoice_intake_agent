"use client";

import { setNonce } from "get-nonce";

/**
 * Radix's dialogs lock page scrolling by adding a style element. Under our strict Content Security
 * Policy that element needs this request's nonce, so it is handed to the library once, here.
 */
export function StyleNonce({ nonce }: { nonce: string | null }) {
  if (nonce) setNonce(nonce);
  return null;
}
