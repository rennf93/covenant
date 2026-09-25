import { readFile, rename, writeFile } from "node:fs/promises";
import type { Address } from "viem";
import {
  emptyState,
  computeDerived,
  recomputeDerived,
  type CovenantState,
  type EpochView,
  type StrategyView,
} from "./state.js";

/**
 * Crash-safe state persistence: an atomic JSON snapshot written after every
 * committed batch and reloaded on startup, so restarts and backfills resume
 * instead of silently rebuilding (or worse, silently diverging).
 *
 * Bigints travel as strings; the derived view is recomputed on load so the
 * file stays minimal and can never disagree with the epochs it summarizes.
 */

/** Default snapshot location (STATE_FILE env overrides). */
export const DEFAULT_STATE_FILE = "./covenant-state.json";

interface PersistedEpoch {
  epochIndex: string;
  equity: string | null;
  netFlow: string | null;
  tradesRoot: string | null;
  evidenceUri: string | null;
  status: number;
  committedAt: string | null;
  finalizedAt: string | null;
  challenger: string | null;
  stake: string;
  pnl: string | null;
}

interface PersistedStrategy {
  id: string;
  owner: string;
  name: string;
  bond: string;
  status: number;
  createdAt: string | null;
  epochs: PersistedEpoch[];
}

export interface PersistedState {
  contract: string;
  lastBlock: string;
  strategies: PersistedStrategy[];
}

function epochToPersisted(e: EpochView): PersistedEpoch {
  return {
    epochIndex: e.epochIndex.toString(),
    equity: e.equity?.toString() ?? null,
    netFlow: e.netFlow?.toString() ?? null,
    tradesRoot: e.tradesRoot,
    evidenceUri: e.evidenceUri,
    status: e.status,
    committedAt: e.committedAt,
    finalizedAt: e.finalizedAt?.toString() ?? null,
    challenger: e.challenger,
    stake: e.stake.toString(),
    pnl: e.pnl?.toString() ?? null,
  };
}

export function serializeState(state: CovenantState): PersistedState {
  return {
    contract: state.contract,
    lastBlock: state.lastBlock.toString(),
    strategies: [...state.strategies.values()].map((s) => ({
      id: s.id.toString(),
      owner: s.owner,
      name: s.name,
      bond: s.bond.toString(),
      status: s.status,
      createdAt: s.createdAt,
      epochs: [...s.epochs.values()].map(epochToPersisted),
    })),
  };
}

export function deserializeState(data: PersistedState): CovenantState {
  const state = emptyState(data.contract as Address);
  state.lastBlock = BigInt(data.lastBlock);
  for (const ps of data.strategies) {
    const strategy: StrategyView = {
      id: BigInt(ps.id),
      owner: ps.owner as Address,
      name: ps.name,
      bond: BigInt(ps.bond),
      status: ps.status,
      createdAt: ps.createdAt,
      epochs: new Map(),
      derived: computeDerived([]),
    };
    for (const pe of ps.epochs) {
      const epoch: EpochView = {
        epochIndex: BigInt(pe.epochIndex),
        equity: pe.equity === null ? null : BigInt(pe.equity),
        netFlow: pe.netFlow === null ? null : BigInt(pe.netFlow),
        tradesRoot: pe.tradesRoot,
        evidenceUri: pe.evidenceUri,
        status: pe.status,
        committedAt: pe.committedAt,
        finalizedAt: pe.finalizedAt === null ? null : BigInt(pe.finalizedAt),
        challenger: (pe.challenger as Address | null) ?? null,
        stake: BigInt(pe.stake),
        pnl: pe.pnl === null ? null : BigInt(pe.pnl),
      };
      strategy.epochs.set(epoch.epochIndex.toString(), epoch);
    }
    recomputeDerived(strategy);
    state.strategies.set(strategy.id.toString(), strategy);
  }
  return state;
}

/**
 * Writes the snapshot atomically: serialize to a temp file in the same
 * directory, then rename over the target. A crash mid-write leaves the old
 * snapshot intact, never a half-written one.
 */
export async function saveStateFile(path: string, state: CovenantState): Promise<void> {
  const tmp = `${path}.${process.pid}.tmp`;
  await writeFile(tmp, JSON.stringify(serializeState(state)));
  await rename(tmp, path);
}

/** Loads the snapshot; null when it does not exist. Parse errors propagate. */
export async function loadStateFile(path: string): Promise<CovenantState | null> {
  let raw: string;
  try {
    raw = await readFile(path, "utf8");
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw e;
  }
  return deserializeState(JSON.parse(raw) as PersistedState);
}
