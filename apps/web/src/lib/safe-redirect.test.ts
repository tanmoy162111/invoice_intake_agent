import { describe, expect, test } from "vitest";

import { safeNext } from "./safe-redirect";

describe("safeNext", () => {
  test("keeps a path on this site", () => {
    expect(safeNext("/queue")).toBe("/queue");
    expect(safeNext("/invoices/abc?tab=fields")).toBe("/invoices/abc?tab=fields");
  });
  test("anything else goes to the queue", () => {
    for (const bad of [
      "https://evil.example",
      "//evil.example",
      "/\\evil.example",
      "\\\\evil.example",
      "javascript:alert(1)",
      "queue",
      "",
      null,
      undefined,
      "/login",
      "/queue\nSet-Cookie: x=1",
      "/%0d%0aX",
    ]) {
      expect(safeNext(bad as string | null | undefined)).toBe("/queue");
    }
  });
});
