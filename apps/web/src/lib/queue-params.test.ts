import { describe, expect, test } from "vitest";

import { PAGE_SIZE, parseQueueParams, queueHref, toApiQuery } from "./queue-params";

describe("parseQueueParams", () => {
  test("defaults to the invoices that need a person, first page", () => {
    expect(parseQueueParams({})).toEqual({ status: "needs_review", code: null, supplier: null, page: 1 });
  });
  test("reads valid filters", () => {
    const uuid = "0d9a7d0e-5e4c-4b8e-9d3a-4d3f5b8a1c11";
    expect(
      parseQueueParams({ status: "failed", code: "NO_PO", supplier: uuid, page: "3" }),
    ).toEqual({ status: "failed", code: "NO_PO", supplier: uuid, page: 3 });
  });
  test("ignores anything that is not valid instead of passing it on", () => {
    expect(
      parseQueueParams({ status: "hacked", code: "NOT_A_CODE", supplier: "x", page: "-4" }),
    ).toEqual({ status: "needs_review", code: null, supplier: null, page: 1 });
    expect(parseQueueParams({ page: "1e9" }).page).toBe(1);
    expect(parseQueueParams({ page: "2.5" }).page).toBe(1);
    expect(parseQueueParams({ status: ["failed", "cleared"] }).status).toBe("failed");
  });
});

describe("toApiQuery", () => {
  test("turns filters into the API's paging and filters", () => {
    const q = toApiQuery({ status: "cleared", code: "NO_PO", supplier: null, page: 3 });
    expect(q).toEqual({ status: ["cleared"], code: "NO_PO", limit: PAGE_SIZE, offset: 2 * PAGE_SIZE });
    expect(toApiQuery({ status: "needs_review", code: null, supplier: null, page: 1 })).toEqual({
      status: ["needs_review"],
      limit: PAGE_SIZE,
      offset: 0,
    });
  });
});

describe("queueHref", () => {
  test("keeps the filters that differ from the defaults", () => {
    expect(queueHref({ status: "needs_review", code: null, supplier: null, page: 1 })).toBe("/queue");
    expect(queueHref({ status: "failed", code: "NO_PO", supplier: null, page: 2 })).toBe(
      "/queue?status=failed&code=NO_PO&page=2",
    );
  });
  test("changing a filter goes back to the first page", () => {
    const base = { status: "needs_review" as const, code: null, supplier: null, page: 4 };
    expect(queueHref({ ...base, code: "NO_PO" }, { resetPage: true })).toBe("/queue?code=NO_PO");
  });
});
