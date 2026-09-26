export const SESSION_COOKIE = "intake_session";
const MAX_AGE = 86400;

export type CookieEnv = { production: boolean; insecure: boolean };

/**
 * The session cookie: unreadable by scripts, never sent with a request from another site. Secure in
 * production, unless told the site is served over plain http (a local demo).
 */
export function sessionCookieOptions(ttlSeconds: number, env: CookieEnv) {
  return {
    httpOnly: true,
    sameSite: "strict" as const,
    path: "/",
    secure: env.production && !env.insecure,
    maxAge: Math.min(Math.max(Math.floor(ttlSeconds), 0), MAX_AGE),
  };
}
