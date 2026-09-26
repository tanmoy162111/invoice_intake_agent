import { describe, expect, test } from "vitest";

import { SESSION_COOKIE, sessionCookieOptions } from "./cookie";

describe("sessionCookieOptions", () => {
  test("the session is never readable by scripts and never sent to another site", () => {
    const o = sessionCookieOptions(28800, { production: true, insecure: false });
    expect(o).toMatchObject({ httpOnly: true, sameSite: "strict", path: "/", maxAge: 28800, secure: true });
  });
  test("secure is off outside production, or when explicitly told (plain-http demos)", () => {
    expect(sessionCookieOptions(60, { production: false, insecure: false }).secure).toBe(false);
    expect(sessionCookieOptions(60, { production: true, insecure: true }).secure).toBe(false);
  });
  test("the lifetime is never negative or absurd", () => {
    expect(sessionCookieOptions(-5, { production: true, insecure: false }).maxAge).toBe(0);
    expect(sessionCookieOptions(10 ** 9, { production: true, insecure: false }).maxAge).toBe(86400);
  });
  test("has a fixed name", () => {
    expect(SESSION_COOKIE).toBe("intake_session");
  });
});
