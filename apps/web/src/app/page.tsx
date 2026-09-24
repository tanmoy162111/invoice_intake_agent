import { getHealth } from "@/lib/api/client";

export const dynamic = "force-dynamic";

export default async function Home() {
  const health = await getHealth();
  const healthy = health?.status === "ok";

  return (
    <main className="mx-auto max-w-2xl p-8">
      <h1 className="text-2xl font-semibold">Invoice Intake Agent</h1>
      <p className="mt-4" data-testid="api-status">
        API: {healthy ? "healthy" : "unreachable"}
      </p>
    </main>
  );
}
