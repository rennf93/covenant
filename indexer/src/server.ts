import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { CovenantState, StrategyView } from "./state.js";

/**
 * JSON API over indexed state.
 *   GET /health               -> liveness + last indexed block
 *   GET /strategies           -> leaderboard rows, best return first
 *                              (?window=7d|30d|all default all, ?limit, ?offset)
 *   GET /strategies/:id       -> full strategy with epochs
 *   GET /stream               -> Server-Sent Events, full snapshot on connect
 *                              and after every committed batch
 *
 * No framework: the surface is small, typed, and dependency-free.
 */

const CORS_HEADERS = { "access-control-allow-origin": "*" } as const;

export function jsonResponse(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json", ...CORS_HEADERS });
  res.end(toJson(body));
}

/** Stringifies with bigint and Map support (the API's single JSON dialect). */
export function toJson(body: unknown): string {
  return JSON.stringify(body, (_key, value) =>
    typeof value === "bigint" ? value.toString() : value instanceof Map ? Object.fromEntries(value) : value,
  );
}

/** USDG uses 6 decimals on every chain it deploys to. */
const USDG_SCALE = 1e6;
/** Trend sparkline cap: at most the last 32 finalized checkpoints. */
const SPARK_MAX_POINTS = 32;
const RETURN_WAD = 10n ** 18n;
const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 500;

/**
 * Finalized-epoch equity series for the leaderboard trend column: plain human
 * numbers (equity is bigint base units), ascending epoch order, capped to the
 * last 32 points. Null below 2 finalized points so the UI never draws a line
 * from a single checkpoint.
 */
export function sparkSeries(s: StrategyView): number[] | null {
  const points = [...s.epochs.values()]
    .filter((e) => e.status === 1 && e.equity !== null)
    .sort((a, b) => (a.epochIndex < b.epochIndex ? -1 : a.epochIndex > b.epochIndex ? 1 : 0))
    .slice(-SPARK_MAX_POINTS)
    .map((e) => Number(e.equity) / USDG_SCALE);
  return points.length >= 2 ? points : null;
}

/**
 * Per-strategy return over the window: sum of pnl from finalized checkpoints
 * whose finalizedAt falls in the window, divided by the first such equity
 * (1e18 fixed point, mirroring the reducer's headline return). No finalized
 * checkpoints in the window (or a zero base) -> null: such strategies sort
 * last, never first.
 */
export function windowReturn(s: StrategyView, cutoffSec: bigint | null): bigint | null {
  const inWindow = [...s.epochs.values()]
    .filter((e) => e.status === 1)
    .filter((e) => cutoffSec === null || (e.finalizedAt !== null && e.finalizedAt >= cutoffSec))
    .sort((a, b) => (a.epochIndex < b.epochIndex ? -1 : 1));
  if (inWindow.length === 0) return null;
  const base = inWindow[0]!.equity ?? 0n;
  if (base === 0n) return null;
  const pnlSum = inWindow.reduce((acc, e) => acc + (e.pnl ?? 0n), 0n);
  const absBase = base < 0n ? -base : base;
  return (pnlSum * RETURN_WAD) / absBase;
}

export function leaderboardRow(s: StrategyView, windowReturnWad: bigint | null = s.derived.returnWad) {
  return {
    id: s.id.toString(),
    owner: s.owner,
    name: s.name,
    status: s.status,
    bond: s.bond.toString(),
    derived: {
      ...s.derived,
      equity: s.derived.equity.toString(),
      cumulativePnl: s.derived.cumulativePnl.toString(),
      returnWad: s.derived.returnWad?.toString() ?? null,
    },
    finalizedEpochs: s.derived.finalizedEpochs,
    totalEpochs: s.epochs.size,
    spark: sparkSeries(s),
    windowReturnWad: windowReturnWad?.toString() ?? null,
  };
}

/**
 * Parses the window query param: seconds for `Nd` forms, null for `all`
 * (no cutoff), undefined when invalid.
 */
export function windowSeconds(w: string): number | null | undefined {
  if (w === "all") return null;
  const m = /^(\d+)d$/.exec(w);
  if (m === null) return undefined;
  return Number(m[1]) * 86_400;
}

function pagination(url: URL): { limit: number; offset: number } {
  const rawLimit = Number(url.searchParams.get("limit") ?? DEFAULT_LIMIT);
  const rawOffset = Number(url.searchParams.get("offset") ?? 0);
  const limit = Number.isFinite(rawLimit) ? Math.min(MAX_LIMIT, Math.max(1, Math.floor(rawLimit))) : DEFAULT_LIMIT;
  const offset = Number.isFinite(rawOffset) ? Math.max(0, Math.floor(rawOffset)) : 0;
  return { limit, offset };
}

function byEpochIndex(a: { epochIndex: bigint }, b: { epochIndex: bigint }): number {
  return a.epochIndex < b.epochIndex ? -1 : a.epochIndex > b.epochIndex ? 1 : 0;
}

/** Best windowed return first; null returns sort last (then by id for stability). */
function byWindowReturn(state: CovenantState, cutoffSec: bigint | null) {
  const ret = (s: StrategyView) => windowReturn(s, cutoffSec);
  return (a: StrategyView, b: StrategyView): number => {
    const ra = ret(a);
    const rb = ret(b);
    if (ra === null && rb === null) return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    if (ra === null) return 1;
    if (rb === null) return -1;
    return rb > ra ? 1 : rb < ra ? -1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  };
}

/**
 * Tiny pub/sub so the transport can push snapshots to SSE subscribers after
 * each committed batch. Dependency-free by design (one Set).
 */
export interface Hub {
  subscribe(fn: () => void): () => void;
  emit(): void;
}

export function createHub(): Hub {
  const subscribers = new Set<() => void>();
  return {
    subscribe(fn: () => void): () => void {
      subscribers.add(fn);
      return () => subscribers.delete(fn);
    },
    emit(): void {
      for (const fn of subscribers) fn();
    },
  };
}

export function startApi(state: CovenantState, port: number, hub?: Hub): Server {
  /** Full leaderboard snapshot in the default (all-time) window order. */
  const snapshot = (): string => {
    const rows = [...state.strategies.values()].sort(byWindowReturn(state, null)).map((s) => leaderboardRow(s));
    return toJson({ rows });
  };

  return createServer((req: IncomingMessage, res: ServerResponse) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (url.pathname === "/health") {
      jsonResponse(res, 200, { ok: true, lastBlock: state.lastBlock.toString() });
      return;
    }
    if (url.pathname === "/strategies" && req.method === "GET") {
      const rawWindow = url.searchParams.get("window") ?? "all";
      const win = windowSeconds(rawWindow);
      if (win === undefined) {
        jsonResponse(res, 400, { error: "invalid window; use 7d, 30d, or all" });
        return;
      }
      const cutoff = win === null ? null : BigInt(Math.floor(Date.now() / 1000)) - BigInt(win);
      const { limit, offset } = pagination(url);
      const sorted = [...state.strategies.values()].sort(byWindowReturn(state, cutoff));
      const rows = sorted.slice(offset, offset + limit).map((s) => leaderboardRow(s, windowReturn(s, cutoff)));
      jsonResponse(res, 200, { rows, pagination: { total: sorted.length, limit, offset } });
      return;
    }
    const match = /^\/strategies\/(\d+)$/.exec(url.pathname);
    if (match) {
      const strategy = state.strategies.get(match[1]!);
      if (!strategy) {
        jsonResponse(res, 404, { error: "unknown strategy" });
        return;
      }
      jsonResponse(res, 200, {
        ...leaderboardRow(strategy),
        epochs: [...strategy.epochs.values()]
          .sort(byEpochIndex)
          .map((e) => ({
            epochIndex: e.epochIndex.toString(),
            equity: e.equity?.toString() ?? null,
            netFlow: e.netFlow?.toString() ?? null,
            tradesRoot: e.tradesRoot,
            evidenceUri: e.evidenceUri,
            status: e.status,
            committedAt: e.committedAt,
            challenger: e.challenger,
            stake: e.stake.toString(),
            pnl: e.pnl?.toString() ?? null,
          })),
      });
      return;
    }
    if (url.pathname === "/stream") {
      res.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        connection: "keep-alive",
        ...CORS_HEADERS,
      });
      const send = (): void => {
        if (res.destroyed) return;
        res.write(`event: snapshot\ndata: ${snapshot()}\n\n`);
      };
      send(); // connect = immediate full snapshot
      const unsubscribe = hub !== undefined ? hub.subscribe(send) : () => {};
      const keepalive = setInterval(() => {
        if (!res.destroyed) res.write(": keepalive\n\n");
      }, 15_000);
      req.on("close", () => {
        clearInterval(keepalive);
        unsubscribe();
      });
      return;
    }
    jsonResponse(res, 404, { error: "not found" });
  }).listen(port);
}
