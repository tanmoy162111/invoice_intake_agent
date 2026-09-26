const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DASH = "—";

/** Decimal places of a currency's minor unit (cents for USD, none for JPY). Unknown means 2. */
export function minorUnitDigits(currency: string | null | undefined): number {
  if (!currency) return 2;
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).resolvedOptions()
      .maximumFractionDigits ?? 2;
  } catch {
    return 2;
  }
}

/** An amount held in minor units, for display only (decisions are made by the API, in integers). */
export function formatMoney(minor: number | null | undefined, currency: string | null | undefined): string {
  if (minor === null || minor === undefined) return DASH;
  const digits = minorUnitDigits(currency);
  const value = minor / 10 ** digits;
  if (currency) {
    try {
      return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(value);
    } catch {
      // an unknown currency code: fall through to a bare number rather than guess a symbol
    }
  }
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** A date or timestamp as "31 May 2026" (UTC, so it never shifts with the reader's time zone). */
export function formatDate(value: string | null | undefined): string {
  if (!value) return DASH;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return DASH;
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** How long ago, in the largest sensible unit: "just now", "45m", "5h", "3d", "16w". */
export function formatAge(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return DASH;
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return DASH;
  const seconds = Math.max(0, Math.floor((now.getTime() - then) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)}d`;
  return `${Math.floor(seconds / (7 * 86400))}w`;
}

export function formatPercent(fraction: number | null | undefined): string {
  if (fraction === null || fraction === undefined) return DASH;
  return `${Math.round(fraction * 100)}%`;
}
