const HOME = "/queue";

/** Where to go after signing in: only a path on this site, never another address or a loop. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/")) return HOME;
  if (next.startsWith("//") || next.includes("\\")) return HOME;
  if (/[\u0000-\u001f\u007f]/.test(next) || /%0[0-9a-f]|%1[0-9a-f]|%7f/i.test(next)) return HOME;
  if (next === "/login" || next.startsWith("/login?") || next.startsWith("/login/")) return HOME;
  return next;
}
