const PUBLIC = new Set(["/login", "/icon.svg", "/favicon.ico"]);

/** Pages that need no session: the sign-in page and the site icon. Exact matches only. */
export function isPublicPath(pathname: string): boolean {
  return PUBLIC.has(pathname);
}
