import { getPageImage } from "@/lib/api/server";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * A page of the document, fetched with the reviewer's session and passed on. The browser never
 * holds the API token, and never talks to the API directly.
 */
export async function GET(_request: Request, { params }: { params: Promise<{ id: string; page: string }> }) {
  const { id, page } = await params;
  if (!UUID.test(id) || !/^\d{1,3}$/.test(page)) return new Response("Not found", { status: 404 });
  const upstream = await getPageImage(id, Number(page));
  if (!upstream.ok || !upstream.body) return new Response("Not found", { status: 404 });
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "image/png",
      "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
