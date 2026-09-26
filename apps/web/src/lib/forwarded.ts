/**
 * The visitor's address as a proxy in front of this server reported it, to pass to the API so its
 * login throttle can tell visitors apart. Only when the operator says such a proxy exists
 * (TRUST_FORWARDED_FOR=1): otherwise the header is whatever the visitor typed.
 */
export function forwardedFor(header: string | null, trust: boolean): string | undefined {
  if (!trust || !header) return undefined;
  const value = header.trim();
  if (value.length === 0 || value.length > 500 || /[\u0000-\u001f\u007f]/.test(value)) return undefined;
  return value;
}
