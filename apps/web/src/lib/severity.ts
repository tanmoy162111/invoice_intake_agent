import type { components } from "./api/schema";

export type Severity = components["schemas"]["Severity"];

/** Worst first: a block needs a written note, a review needs a person, info is only shown. */
export const SEVERITY_ORDER: readonly Severity[] = ["block", "review", "info"];

const RANK: Record<Severity, number> = { block: 3, review: 2, info: 1 };

export function severityRank(severity: Severity | null | undefined): number {
  return severity ? RANK[severity] : 0;
}

export function worstSeverity(all: readonly Severity[]): Severity | null {
  let worst: Severity | null = null;
  for (const s of all) if (severityRank(s) > severityRank(worst)) worst = s;
  return worst;
}
