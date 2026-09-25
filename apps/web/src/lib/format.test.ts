import { describe, expect, test } from "vitest";

import { formatAge, formatDate, formatMoney, formatPercent, minorUnitDigits } from "./format";

describe("formatMoney", () => {
  test("shows minor units in the currency with its own decimals", () => {
    expect(formatMoney(123450, "USD")).toBe("$1,234.50");
    expect(formatMoney(123450, "EUR")).toBe("€1,234.50");
    expect(formatMoney(5000, "JPY")).toBe("¥5,000");
    expect(formatMoney(-500, "USD")).toBe("-$5.00");
    expect(formatMoney(0, "GBP")).toBe("£0.00");
  });
  test("an unknown or missing value is a dash, never a guess", () => {
    expect(formatMoney(null, "USD")).toBe("—");
    expect(formatMoney(undefined, "USD")).toBe("—");
    expect(formatMoney(100, null)).toBe("1.00");
    expect(formatMoney(100, "NOTACODE")).toBe("1.00");
  });
  test("minor unit digits follow the currency", () => {
    expect(minorUnitDigits("USD")).toBe(2);
    expect(minorUnitDigits("JPY")).toBe(0);
    expect(minorUnitDigits(null)).toBe(2);
  });
  test("large amounts keep every digit", () => {
    expect(formatMoney(99999999999999, "USD")).toBe("$999,999,999,999.99");
  });
});

describe("formatDate", () => {
  test("a date is shown in a fixed readable form", () => {
    expect(formatDate("2026-05-31")).toBe("31 May 2026");
    expect(formatDate("2026-01-02T10:00:00Z")).toBe("2 Jan 2026");
  });
  test("nothing or nonsense is a dash", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("")).toBe("—");
    expect(formatDate("not a date")).toBe("—");
  });
});

describe("formatAge", () => {
  const now = new Date("2026-09-26T12:00:00Z");
  test("says how long ago in the largest sensible unit", () => {
    expect(formatAge("2026-09-26T11:59:40Z", now)).toBe("just now");
    expect(formatAge("2026-09-26T11:15:00Z", now)).toBe("45m");
    expect(formatAge("2026-09-26T07:00:00Z", now)).toBe("5h");
    expect(formatAge("2026-09-23T12:00:00Z", now)).toBe("3d");
    expect(formatAge("2026-06-01T12:00:00Z", now)).toBe("16w");
  });
  test("a future or invalid time is not negative", () => {
    expect(formatAge("2026-09-27T12:00:00Z", now)).toBe("just now");
    expect(formatAge("garbage", now)).toBe("—");
  });
});

describe("formatPercent", () => {
  test("a 0 to 1 confidence becomes a whole percent", () => {
    expect(formatPercent(0.75)).toBe("75%");
    expect(formatPercent(0.999)).toBe("100%");
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(null)).toBe("—");
  });
});
