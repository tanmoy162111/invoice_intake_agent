import { afterEach, expect, test, vi } from "vitest";

import { getHealth } from "./client";

afterEach(() => vi.unstubAllGlobals());

test("getHealth returns the parsed body when the API answers", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "ok" }) }));
  expect(await getHealth()).toEqual({ status: "ok" });
});

test("getHealth returns null when the API is down", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
  expect(await getHealth()).toBeNull();
});
