# 0001 — Pin TypeScript 5.9 and ESLint 9 in the web app
Date: 2026-09-25 · Status: accepted

## Context
`pnpm add` resolved TypeScript 7.0 and ESLint 10. `openapi-typescript` (our API type generator)
and `typescript-eslint` need the JS compiler API that TypeScript 7 no longer ships, and
`eslint-plugin-react` (via `eslint-config-next`) crashes on ESLint 10.

## Decision
Pin `typescript@~5.9` and `eslint@^9` in `apps/web`.

## Why
Both tools are on the critical path (`pnpm gen:api`, `pnpm lint`). Rejected: TypeScript 6 side-by-side
with 7 (extra setup for no benefit at this size).

## Consequences
Revisit when `typescript-eslint`, `openapi-typescript` and `eslint-plugin-react` support the newer versions.
