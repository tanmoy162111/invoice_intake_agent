import { NextResponse, type NextRequest } from "next/server";

import { buildCsp, newNonce } from "./lib/csp";
import { SESSION_COOKIE } from "./lib/cookie";

/**
 * Runs before every page: gives each response its own CSP nonce and security headers, and sends a
 * visitor with no session cookie to the login. This is only a quick check (the API is what really
 * decides): a stale or forged cookie gets a 401 from the API and lands on the login again.
 */
export function proxy(request: NextRequest) {
  const nonce = newNonce();
  const csp = buildCsp(nonce, { dev: process.env.NODE_ENV === "development" });
  const { pathname, search } = request.nextUrl;

  if (pathname !== "/login" && !request.cookies.has(SESSION_COOKIE)) {
    const to = new URL("/login", request.url);
    if (request.method === "GET" && pathname !== "/") to.searchParams.set("next", pathname + search);
    const redirect = NextResponse.redirect(to);
    redirect.headers.set("Content-Security-Policy", csp);
    return redirect;
  }

  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  response.headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  response.headers.set("Cross-Origin-Opener-Policy", "same-origin");
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
