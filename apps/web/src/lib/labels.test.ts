import { describe, expect, test } from "vitest";

import type { components } from "./api/schema";
import {
  CHECK_LABELS,
  EXCEPTION_CODES,
  EXCEPTION_LABELS,
  FIELD_LABELS,
  ROUTING_REASON_LABELS,
  STATUS_LABELS,
  fieldLabel,
} from "./labels";
import { SEVERITY_ORDER, severityRank, worstSeverity } from "./severity";

type Code = components["schemas"]["ExceptionCode"];

describe("exception labels", () => {
  test("every exception code has a plain-words name and a one-line meaning", () => {
    for (const code of EXCEPTION_CODES) {
      const label = EXCEPTION_LABELS[code as Code];
      expect(label.title.length).toBeGreaterThan(3);
      expect(label.meaning.length).toBeGreaterThan(10);
      expect(label.title).not.toMatch(/_/);
    }
    expect(EXCEPTION_CODES).toHaveLength(18);
  });
});

describe("status, routing and check labels", () => {
  test("every invoice status has a label and a tone", () => {
    for (const status of [
      "received", "extracting", "extracted", "checking", "cleared", "needs_review",
      "approved", "rejected", "exported", "failed",
    ] as const) {
      expect(STATUS_LABELS[status].label).toBeTruthy();
      expect(STATUS_LABELS[status].tone).toMatch(/^(ok|warn|bad|info|neutral)$/);
    }
  });
  test("every routing reason the API can give has words", () => {
    for (const reason of [
      "BANK_DETAILS_CHANGED", "OPEN_EXCEPTIONS", "LOW_CONFIDENCE", "NO_TOTAL", "CREDIT_NOTE",
      "ZERO_TOTAL", "NO_LIMIT", "ABOVE_LIMIT", "MISSING_CHECKS",
    ]) {
      expect(ROUTING_REASON_LABELS[reason]).toBeTruthy();
    }
  });
  test("an unknown reason or check is shown as readable text, not dropped", () => {
    expect(ROUTING_REASON_LABELS.SOMETHING_NEW).toBeUndefined();
    expect(CHECK_LABELS.PO_OVERBILLED).toBeTruthy();
  });
  test("field names read like a person would say them", () => {
    expect(fieldLabel("supplier_bank_account")).toBe("Bank account");
    expect(fieldLabel("invoice_date")).toBe("Invoice date");
    expect(fieldLabel("some_new_field")).toBe("Some new field");
    expect(FIELD_LABELS.total).toBe("Total");
  });
});

describe("severity", () => {
  test("blocks come first, then reviews, then info", () => {
    expect(SEVERITY_ORDER).toEqual(["block", "review", "info"]);
    expect(severityRank("block")).toBeGreaterThan(severityRank("review"));
    expect(severityRank("review")).toBeGreaterThan(severityRank("info"));
    expect(severityRank(null)).toBe(0);
  });
  test("the worst of several is the one shown", () => {
    expect(worstSeverity(["info", "review", "block"])).toBe("block");
    expect(worstSeverity(["info"])).toBe("info");
    expect(worstSeverity([])).toBeNull();
  });
});
