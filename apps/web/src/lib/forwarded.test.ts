import { describe, expect, test } from "vitest";

import { forwardedFor } from "./forwarded";

describe("forwardedFor", () => {
  test("passes the visitor's address on only when a proxy in front is declared", () => {
    expect(forwardedFor("198.51.100.9", true)).toBe("198.51.100.9");
    expect(forwardedFor("198.51.100.9", false)).toBeUndefined();
  });
  test("a missing or absurd header is dropped", () => {
    expect(forwardedFor(null, true)).toBeUndefined();
    expect(forwardedFor("", true)).toBeUndefined();
    expect(forwardedFor("x".repeat(600), true)).toBeUndefined();
    expect(forwardedFor("a\r\nInjected: 1", true)).toBeUndefined();
  });
  test("a chain is kept as it came", () => {
    expect(forwardedFor("198.51.100.9, 10.0.0.2", true)).toBe("198.51.100.9, 10.0.0.2");
  });
});
