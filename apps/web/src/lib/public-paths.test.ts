import { describe, expect, test } from "vitest";

import { isPublicPath } from "./public-paths";

describe("isPublicPath", () => {
  test("the sign-in page and the site icon need no session", () => {
    expect(isPublicPath("/login")).toBe(true);
    expect(isPublicPath("/icon.svg")).toBe(true);
    expect(isPublicPath("/favicon.ico")).toBe(true);
  });
  test("everything else does, including look-alikes", () => {
    for (const p of ["/", "/queue", "/invoices/x", "/upload", "/api/pages/a/1", "/login/x", "/loginx", "/icon.svg/x", "/../queue"]) {
      expect(isPublicPath(p)).toBe(false);
    }
  });
});
