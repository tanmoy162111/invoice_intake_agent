import { describe, expect, test } from "vitest";

import { buildCsp, newNonce } from "./csp";

describe("buildCsp", () => {
  const csp = buildCsp("abc123", { dev: false });
  test("scripts run only with this request's nonce", () => {
    expect(csp).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(csp).not.toContain("unsafe-eval");
    expect(csp).not.toMatch(/script-src[^;]*unsafe-inline/);
  });
  test("nothing can be framed, embedded or posted to another site", () => {
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain("form-action 'self'");
    expect(csp).toContain("default-src 'self'");
  });
  test("images may be this site's own, blobs and data URLs only", () => {
    expect(csp).toContain("img-src 'self' blob: data:");
  });
  test("style attributes are allowed (React renders them) but style elements need the nonce", () => {
    expect(csp).toContain("style-src 'self' 'nonce-abc123'");
    expect(csp).toContain("style-src-attr 'unsafe-inline'");
  });
  test("development also allows eval for React's debugging", () => {
    expect(buildCsp("n", { dev: true })).toContain("'unsafe-eval'");
  });
  test("it is one line with no stray whitespace", () => {
    expect(csp).not.toMatch(/\n|\s{2,}/);
  });
});

describe("newNonce", () => {
  test("is unpredictable and different every time", () => {
    const a = newNonce();
    const b = newNonce();
    expect(a).not.toBe(b);
    expect(a).toMatch(/^[A-Za-z0-9+/=]{20,}$/);
  });
});
