/**
 * Server-side data access: the indexer's JSON API is the single source the
 * site reads from. Contract reads (receipt verification) happen client-side
 * via viem against the configured RPC.
 */

export interface LeaderboardRow {
  id: string;
  owner: string;
  name: string;
  status: number;
  bond: string;
  derived: {
    equity: string;
    cumulativePnl: string;
    returnWad: string | null;
    finalizedEpochs: number;
    pendingEpochs: number;
    challengedEpochs: number;
    invalidatedEpochs: number;
  };
  finalizedEpochs: number;
  totalEpochs: number;
}

export interface EpochRow {
  epochIndex: string;
  equity: string | null;
  netFlow: string | null;
  tradesRoot: string | null;
  evidenceUri: string | null;
  status: number;
  committedAt: string | null;
  pnl: string | null;
}

export interface StrategyDetail extends LeaderboardRow {
  epochs: EpochRow[];
}

export function indexerUrl(): string {
  return process.env.INDEXER_URL ?? "http://127.0.0.1:8787";
}

export async function fetchLeaderboard(): Promise<LeaderboardRow[]> {
  const res = await fetch(`${indexerUrl()}/strategies`, { next: { revalidate: 15 } });
  if (!res.ok) throw new Error(`indexer ${res.status}`);
  const body = (await res.json()) as { rows: LeaderboardRow[] };
  return body.rows;
}

/** True when the indexer is the synthetic demo server (no contract deployed). */
export async function fetchDemoFlag(): Promise<boolean> {
  try {
    const res = await fetch(`${indexerUrl()}/strategies`, { next: { revalidate: 15 } });
    if (!res.ok) return false;
    const body = (await res.json()) as { demo?: boolean };
    return body.demo === true;
  } catch {
    return false;
  }
}

export async function fetchStrategy(id: string): Promise<StrategyDetail> {
  const res = await fetch(`${indexerUrl()}/strategies/${encodeURIComponent(id)}`, {
    next: { revalidate: 15 },
  });
  if (!res.ok) throw new Error(`indexer ${res.status}`);
  return (await res.json()) as StrategyDetail;
}

/** USDG has 6 decimals on every chain it deploys to. */
export function formatUsdg(baseUnits: string | null): string {
  if (baseUnits === null) return "-";
  const neg = baseUnits.startsWith("-");
  const raw = neg ? baseUnits.slice(1) : baseUnits;
  const [whole, frac = ""] = raw.split(".");
  const padded = frac.padEnd(6, "0").slice(0, 6);
  const trimmed = padded.replace(/0+$/, "");
  const body = `${whole}.${trimmed || "00"}`;
  return `${neg ? "-" : ""}${body}`;
}

/** 1e18 fixed point return to a percentage string. */
export function formatReturnWad(wad: string | null): string {
  if (wad === null) return "-";
  const pct = Number(wad) / 1e16; // 1e18 wad -> percent, 2 decimals
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

export function statusPill(status: number): { label: string; cls: string } {
  if (status === 0) return { label: "active", cls: "ok" };
  return { label: "suspended", cls: "bad" };
}

export function checkpointPill(status: number): { label: string; cls: string } {
  switch (status) {
    case 0:
      return { label: "pending", cls: "warn" };
    case 1:
      return { label: "finalized", cls: "ok" };
    case 2:
      return { label: "challenged", cls: "warn" };
    default:
      return { label: "invalidated", cls: "bad" };
  }
}
