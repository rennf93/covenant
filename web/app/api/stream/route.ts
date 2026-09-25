/**
 * Browser-facing SSE relay: EventSource cannot reach INDEXER_URL (it is
 * server-side only), so this route pipes the indexer's /stream frames through
 * verbatim. When the indexer is down or predates /stream, the relay answers
 * 502 and the client pill degrades to its offline state.
 */
import { indexerUrl } from "../../../lib/api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetch(`${indexerUrl()}/stream`, {
      headers: { accept: "text/event-stream" },
      cache: "no-store",
    });
  } catch {
    return new Response("indexer stream unavailable", { status: 502 });
  }
  if (!upstream.ok || upstream.body === null) {
    return new Response("indexer stream unavailable", { status: 502 });
  }
  return new Response(upstream.body, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      "x-accel-buffering": "no",
    },
  });
}
