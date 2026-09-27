import { describe, expect, test } from "vitest";

import {
  CORRECTABLE_FIELDS,
  NOTE_MAX,
  cleanNote,
  closeProblem,
  isCorrectable,
  optionalNoteProblem,
  rejectProblem,
} from "./review-input";

describe("cleanNote", () => {
  test("trims, and turns empty into null", () => {
    expect(cleanNote("  paid twice  ")).toBe("paid twice");
    expect(cleanNote("   ")).toBeNull();
    expect(cleanNote(undefined)).toBeNull();
  });
});

describe("closing an exception", () => {
  test("a block exception needs a note, either way it is closed", () => {
    expect(closeProblem("block", "")?.code).toBe("NOTE_REQUIRED");
    expect(closeProblem("block", "   ")?.code).toBe("NOTE_REQUIRED");
    expect(closeProblem("block", "Confirmed with the supplier by phone")).toBeNull();
  });
  test("review and info exceptions can be closed without a note", () => {
    expect(closeProblem("review", "")).toBeNull();
    expect(closeProblem("info", null)).toBeNull();
  });
  test("no note is enormous", () => {
    expect(closeProblem("review", "x".repeat(NOTE_MAX + 1))?.code).toBe("NOTE_TOO_LONG");
    expect(closeProblem("review", "x".repeat(NOTE_MAX))).toBeNull();
  });
});

describe("rejecting and asking", () => {
  test("a rejection needs a reason", () => {
    expect(rejectProblem(" ")?.code).toBe("NOTE_REQUIRED");
    expect(rejectProblem("Duplicate of a paid invoice")).toBeNull();
  });
  test("a request for information may have no note", () => {
    expect(optionalNoteProblem("")).toBeNull();
    expect(optionalNoteProblem("x".repeat(NOTE_MAX + 1))?.code).toBe("NOTE_TOO_LONG");
  });
});

describe("correctable fields", () => {
  test("the bank account is never correctable", () => {
    expect(isCorrectable("supplier_bank_account")).toBe(false);
    expect(isCorrectable("total")).toBe(true);
    expect(CORRECTABLE_FIELDS).toHaveLength(12);
  });
});
