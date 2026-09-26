import "server-only";

import { cookies } from "next/headers";

import { SESSION_COOKIE, sessionCookieOptions } from "./cookie";

const env = () => ({
  production: process.env.NODE_ENV === "production",
  insecure: process.env.COOKIE_INSECURE === "1",
});

/** The reviewer's session token, if signed in. It lives in an httpOnly cookie, never in the page. */
export async function getSessionToken(): Promise<string | null> {
  return (await cookies()).get(SESSION_COOKIE)?.value ?? null;
}

export async function setSession(token: string, ttlSeconds: number): Promise<void> {
  (await cookies()).set(SESSION_COOKIE, token, sessionCookieOptions(ttlSeconds, env()));
}

export async function clearSession(): Promise<void> {
  (await cookies()).set(SESSION_COOKIE, "", { ...sessionCookieOptions(0, env()), maxAge: 0 });
}
