/** A fresh, unpredictable value for every request. */
export function newNonce(): string {
  return btoa(crypto.randomUUID());
}

/**
 * The Content Security Policy: scripts run only with this request's nonce, nothing can frame the
 * page, and forms only post here. React writes style attributes while rendering, so attributes are
 * allowed; style elements still need the nonce.
 */
export function buildCsp(nonce: string, options: { dev: boolean }): string {
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${options.dev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  return directives.join("; ");
}
