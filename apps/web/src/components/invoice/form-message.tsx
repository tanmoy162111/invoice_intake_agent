import type { ActionState } from "@/app/(app)/invoices/[id]/actions";

/** The reason an action was refused, read out to screen readers. Shows nothing until then. */
export function FormMessage({ state }: { state: ActionState }) {
  if (!state || state.ok) return null;
  return (
    <p role="alert" className="rounded-md border border-block/30 bg-block-soft px-3 py-2 text-sm text-block">
      {state.message}
    </p>
  );
}
