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
  /** USDG base units (integer string, 6 decimals). */
  bond: string;
  derived: {
    /** USDG base units; "0" before the first finalized checkpoint. */
    equity: string;
    /** USDG base units. */
    cumulativePnl: string;
    /** 1e18 fixed point return, null without finalized history. */
    returnWad: string | null;
    finalizedEpochs: number;
    pendingEpochs: number;
    challengedEpochs: number;
    invalidatedEpochs: number;
  };
  finalizedEpochs: number;
  totalEpochs: number;
  /**
   * Finalized-epoch equity series in plain human numbers, ascending epoch
   * order, capped to the last 32 points; null below 2 finalized checkpoints
   * so the UI never charts a single point.
   */
  spark: (number | null)[] | null;
  /** Return over the requested /strategies window (1e18 wad); null sorts last. */
  windowReturnWad?: string | null;
}

export interface EpochRow {
  epochIndex: string;
  /** USDG base units, or null before the epoch carries a value. */
  equity: string | null;
  netFlow: string | null;
  tradesRoot: string | null;
  evidenceUri: string | null;
  status: number;
  /** ISO 8601 commit time, null when the block timestamp is unknown. */
  committedAt: string | null;
  pnl: string | null;
  challenger?: string | null;
  /** USDG base units. */
  stake?: string | null;
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

/**
 * USDG base-unit integer string (6 decimals on every chain) to a human
 * string. Pure string math, so typical magnitudes never hit float drift;
 * legacy decimal strings are tolerated by truncating at the point.
 */
export function formatUsdg(baseUnits: string | null): string {
  if (baseUnits === null) return "-";
  const neg = baseUnits.startsWith("-");
  const raw = (neg ? baseUnits.slice(1) : baseUnits).split(".")[0] ?? "0";
  if (!/^\d+$/.test(raw)) return "-";
  const padded = raw.padStart(7, "0"); // at least 0.000001
  const whole = padded.slice(0, -6);
  const frac = padded.slice(-6).replace(/0+$/, "");
  return `${neg ? "-" : ""}${whole}.${frac.length >= 2 ? frac : frac.padEnd(2, "0")}`;
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
