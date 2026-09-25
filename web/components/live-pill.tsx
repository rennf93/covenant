"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

/**
 * LIVE pill in the leaderboard's panel head: opens the /api/stream SSE relay
 * and calls router.refresh() whenever the indexer pushes a snapshot, so the
 * server-rendered table updates itself while you look at it. Refreshes are
 * throttled (the real indexer emits on every new block) and the pill degrades
 * to an honest offline state when the stream is unavailable (demo server has
 * no /stream). The EventSource is closed on unmount.
 */
export default function LivePill() {
  const router = useRouter();
  const [state, setState] = useState<"connecting" | "live" | "offline">("connecting");
  const lastRefresh = useRef(0);
  const pending = useRef<number | null>(null);

  useEffect(() => {
    let closed = false;
    let source: EventSource | null = null;
    const timer = pending;

    function refreshThrottled() {
      if (closed) return;
      const now = Date.now();
      const elapsed = now - lastRefresh.current;
      if (elapsed >= 6000) {
        lastRefresh.current = now;
        router.refresh();
        return;
      }
      if (timer.current !== null) return;
      timer.current = window.setTimeout(() => {
        timer.current = null;
        if (closed) return;
        lastRefresh.current = Date.now();
        router.refresh();
      }, 6000 - elapsed);
    }

    try {
      source = new EventSource("/api/stream");
    } catch {
      setState("offline");
      return;
    }
    source.onopen = () => {
      if (!closed) setState("live");
    };
    source.addEventListener("snapshot", () => {
      if (closed) return;
      setState("live");
      refreshThrottled();
    });
    source.onerror = () => {
      if (!closed) setState("offline");
    };
    return () => {
      closed = true;
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
      source?.close();
    };
  }, [router]);

  const label = state === "live" ? "live" : state === "connecting" ? "connecting" : "offline";
  const title =
    state === "live"
      ? "streaming indexer updates; the page refreshes itself on every snapshot"
      : state === "connecting"
        ? "opening the indexer event stream"
        : "no indexer stream reachable (the demo indexer has none); showing the last loaded data";

  return (
    <span className="chip live-pill" data-state={state} title={title}>
      <span className={state === "offline" ? "dot dot-bad" : "dot dot-ok"} aria-hidden="true" />
      <span className="live-pill-label" role="status" aria-label={`Indexer stream: ${label}`}>
        {label}
      </span>
    </span>
  );
}
