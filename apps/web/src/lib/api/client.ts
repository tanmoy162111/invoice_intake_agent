import type { components } from "./schema";

export type HealthResponse = components["schemas"]["HealthResponse"];

const apiUrl = process.env.API_URL ?? "http://localhost:8000";

export async function getHealth(): Promise<HealthResponse | null> {
  try {
    const res = await fetch(`${apiUrl}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as HealthResponse;
  } catch {
    return null;
  }
}
