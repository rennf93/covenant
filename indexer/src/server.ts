import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { CovenantState, StrategyView } from "./state.js";

/**
 * JSON API over indexed state.
 *   GET /health               -> liveness + last indexed block
 *   GET /strategies           -> leaderboard rows, best return first
 *   GET /strategies/:id       -> full strategy with epochs
 *
 * No framework: the surface is small, typed, and dependency-free.
 */

export function jsonResponse(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body, (_key, value) =>
    typeof value === "bigint" ? value.toString() : value instanceof Map ? Object.fromEntries(value) : value,
  );
  res.writeHead(status, { "content-type": "application/json" });
  res.end(payload);
}

/** USDG uses 6 decimals on every chain it deploys to. */
const USDG_SCALE = 1e6;
/** Trend sparkline cap: at most the last 32 finalized checkpoints. */
const SPARK_MAX_POINTS = 32;

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

export function leaderboardRow(s: StrategyView) {
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
  };
}

export function startApi(state: CovenantState, port: number): Server {
  return createServer((req: IncomingMessage, res: ServerResponse) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (url.pathname === "/health") {
      jsonResponse(res, 200, { ok: true, lastBlock: state.lastBlock.toString() });
      return;
    }
    if (url.pathname === "/strategies" && req.method === "GET") {
      const rows = [...state.strategies.values()]
        .sort((a, b) => {
          const ra = a.derived.returnWad ?? -(2n ** 255n);
          const rb = b.derived.returnWad ?? -(2n ** 255n);
          return rb > ra ? 1 : rb < ra ? -1 : 0;
        })
        .map(leaderboardRow);
      jsonResponse(res, 200, { rows });
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
          .sort((a, b) => (a.epochIndex < b.epochIndex ? -1 : 1))
          .map((e) => ({
            ...e,
            epochIndex: e.epochIndex.toString(),
            equity: e.equity?.toString() ?? null,
            netFlow: e.netFlow?.toString() ?? null,
            pnl: e.pnl?.toString() ?? null,
          })),
      });
      return;
    }
    jsonResponse(res, 404, { error: "not found" });
  }).listen(port);
}
