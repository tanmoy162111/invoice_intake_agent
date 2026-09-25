# 0009 — The web app: a server-held session, a strict CSP, shadcn on Radix, and generated types
Date: 2026-09-26 · Status: accepted

## Context
M8b is the reviewer's screen. It needs a login that keeps the API token out of the browser, a policy against
injected script, accessible interactive components, a look fit to show clients, and API types that cannot drift.
The web app was a health-check stub with no components.

## Decision
1. **Session.** The web server logs in against the API and keeps the returned session token in a cookie named
   `intake_session`: `httpOnly`, `SameSite=Strict`, `Secure` in production (unless `COOKIE_INSECURE=1`, for a
   plain-http demo), lifetime at most the API's. Pages and actions call the API from the server with that token as
   a bearer. The browser never holds a token and never calls the API. Page images pass through a route that adds
   the session.
2. **Guarding.** `proxy.ts` redirects a visitor with no cookie to sign-in (an optimistic check only: the API
   decides, and a stale cookie is bounced by a 401 to sign-in). Only `/login`, `/icon.svg` and `/favicon.ico` are
   public. The sign-in `next` address is accepted only if it is a path on this site.
3. **Content Security Policy** with a per-request nonce set by the proxy: `default-src 'self'`; scripts by nonce
   with `strict-dynamic`; styles by nonce, plus `style-src-attr 'unsafe-inline'` because React writes style
   attributes while rendering; own-site images (and blob and data URLs), fonts and connections only;
   `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`. A library that injects a
   style element without a nonce is not used: sonner (toasts) was removed; Radix's scroll lock is given the nonce
   through `get-nonce`. The policy is not loosened for a library.
4. **Mutations** use Server Actions (which carry Next's origin check) with the session held on the server; there
   is no client-side API token and no CORS need for the browser.
5. **Components**: shadcn/ui patterns over Radix primitives (dialogs, sheets, menus, tabs), styled with
   Tailwind 4 design tokens, icons from lucide, class merging with clsx and tailwind-merge. Chosen over
   hand-rolling because dialogs and menus are where accessibility usually breaks. Every dependency is pinned by
   the lockfile, audited (`pnpm audit`) and permissively licensed.
6. **Look.** A ledger: warm paper, ink, and a vermilion accent; Newsreader for headings and names, Instrument
   Sans for text, IBM Plex Mono with tabular numerals for amounts and identifiers; severities as pressed stamps
   (colour is never the only signal); light, dark and follow-the-device (chosen with a cookie so the server can
   render it). Fonts are self-hosted through npm (no CDN). Motion is a staggered page load and a stamp press,
   and is turned off for `prefers-reduced-motion`.
7. **Key fields first.** The invoice page separates the five key fields (supplier, invoice number, date, total,
   currency) from the rest, and the "not certain" count is of key fields only, because that is what routing acts
   on; a doubtful other field is noted, not alarmed.
8. **Types.** `apps/web/src/lib/api/openapi.json` is a committed snapshot of the API schema and `schema.d.ts` is
   generated from it (`make gen-api`, no server needed). A unit test in the API suite fails if the snapshot is
   out of date. The invoice header became a typed model and the two `DocumentOut` models were renamed so the
   generated names are readable.
9. **Delivery.** M8b is two pull requests: M8b-1 (this: sign-in, queue, invoice, upload; read-only) and M8b-2
   (the actions, a recorded model provider for test mode only, and the browser test in CI).

## Why
A session in a script-readable place, or a policy with `unsafe-inline`, turns any injected markup into a stolen
session. Keeping the token on the server and the policy strict costs a little convenience. Accessible primitives
and a distinct look are part of showing this to clients, not decoration.

## Consequences
- `SameSite=Strict` means a link into the app from another site (an email) first lands on sign-in if the cookie
  was not sent; signing in continues to the requested page.
- A change to the API needs `make gen-api` or the API suite fails.
- Toasts are not available; results are shown in place. (Radix Toast injects no style element and can be used.)
- The Compose demo sets `COOKIE_INSECURE=1`; behind https that variable must be removed.
- The document viewer cannot highlight a field's position: the reader records the page only.
