import type { Address } from "viem";

/**
 * Pure event reducer for Covenant protocol state.
 *
 * The chain is the source of truth; this module derives a queryable model from
 * decoded contract events. It knows nothing about RPC, files, or HTTP so it can
 * be exhaustively unit-tested and reused (backfill, live tail, API, future UI).
 */

export interface EpochView {
  epochIndex: bigint;
  equity: bigint | null;
  netFlow: bigint | null;
  tradesRoot: string | null;
  evidenceUri: string | null;
  /** 0 pending, 1 finalized, 2 challenged, 3 invalidated. */
  status: number;
  committedAt: bigint | null;
  challenger: Address | null;
  stake: bigint;
  pnl: bigint | null;
}

export interface StrategyView {
  id: bigint;
  owner: Address;
  name: string;
  bond: bigint;
  /** 0 active, 1 suspended. */
  status: number;
  createdAt: bigint | null;
  epochs: Map<string, EpochView>;
  /** Derived from finalized epochs only. */
  derived: {
    equity: bigint;
    cumulativePnl: bigint;
    finalizedEpochs: number;
    pendingEpochs: number;
    challengedEpochs: number;
    invalidatedEpochs: number;
    /** Simple return over finalized history, in 1e18 fixed point. */
    returnWad: bigint | null;
  };
}

export interface CovenantState {
  contract: Address;
  /** Block height the state reflects (inclusive). */
  lastBlock: bigint;
  strategies: Map<string, StrategyView>;
}

export function emptyState(contract: Address): CovenantState {
  return { contract, lastBlock: 0n, strategies: new Map() };
}

/** Minimal decoded event shape, compatible with viem's decodeEventLog output. */
export interface DecodedEvent {
  eventName: string;
  args: Record<string, unknown>;
  blockNumber: bigint;
  /** Optional: filled by the transport when the block is known. */
  blockTimestamp?: bigint;
}

function requireStrategy(state: CovenantState, id: unknown): StrategyView {
  const key = String(id);
  const existing = state.strategies.get(key);
  if (existing) return existing;
  const created: StrategyView = {
    id: BigInt(key),
    owner: "0x0000000000000000000000000000000000000000" as Address,
    name: "",
    bond: 0n,
    status: 0,
    createdAt: null,
    epochs: new Map(),
    derived: {
      equity: 0n,
      cumulativePnl: 0n,
      finalizedEpochs: 0,
      pendingEpochs: 0,
      challengedEpochs: 0,
      invalidatedEpochs: 0,
      returnWad: null,
    },
  };
  state.strategies.set(key, created);
  return created;
}

function epochOf(strategy: StrategyView, index: unknown): EpochView {
  const key = String(index);
  const existing = strategy.epochs.get(key);
  if (existing) return existing;
  const created: EpochView = {
    epochIndex: BigInt(key),
    equity: null,
    netFlow: null,
    tradesRoot: null,
    evidenceUri: null,
    status: 0,
    committedAt: null,
    challenger: null,
    stake: 0n,
    pnl: null,
  };
  strategy.epochs.set(key, created);
  return created;
}

/**
 * Recomputes the derived view from finalized epochs. O(epochs); called per
 * relevant event. Total-return convention: pnl_0 includes the initial flow
 * because epoch 0's netFlow is the seed capital (see docs/protocol.md,
 * section 3.4 PnL accounting).
 */
function rederive(strategy: StrategyView): void {
  const d = strategy.derived;
  d.equity = 0n;
  d.cumulativePnl = 0n;
  d.finalizedEpochs = 0;
  d.pendingEpochs = 0;
  d.challengedEpochs = 0;
  d.invalidatedEpochs = 0;
  d.returnWad = null;

  const epochs = [...strategy.epochs.values()].sort(
    (a, b) => (a.epochIndex < b.epochIndex ? -1 : a.epochIndex > b.epochIndex ? 1 : 0),
  );
  let firstEquity: bigint | null = null;
  let lastEquity: bigint | null = null;
  for (const e of epochs) {
    if (e.status === 0) d.pendingEpochs += 1;
    else if (e.status === 2) d.challengedEpochs += 1;
    else if (e.status === 3) d.invalidatedEpochs += 1;
    else if (e.status === 1) {
      d.finalizedEpochs += 1;
      d.equity = e.equity ?? 0n;
      d.cumulativePnl += e.pnl ?? 0n;
      if (firstEquity === null) firstEquity = e.equity ?? 0n;
      lastEquity = e.equity ?? 0n;
    }
  }
  // Headline return: cumulative PnL over the first finalized equity, in 1e18
  // fixed point. PnL-relative (not equity-ratio) so deposits do not inflate it.
  // Per-epoch returns remain derivable from committed checkpoint data.
  if (firstEquity !== null && firstEquity !== 0n) {
    const base = firstEquity < 0n ? -firstEquity : firstEquity;
    d.returnWad = (d.cumulativePnl * 10n ** 18n) / base;
  }
}

/** Applies one decoded event, mutating and returning the state (chainable). */
export function applyEvent(state: CovenantState, event: DecodedEvent): CovenantState {
  const args = event.args;
  switch (event.eventName) {
    case "StrategyRegistered": {
      const strategy = requireStrategy(state, args.strategy_id);
      strategy.owner = args.owner as Address;
      strategy.name = args.name as string;
      strategy.bond = args.bond as bigint;
      strategy.status = 0;
      strategy.createdAt = event.blockTimestamp ?? null;
      break;
    }
    case "EpochCommitted": {
      const strategy = requireStrategy(state, args.strategy_id);
      const epoch = epochOf(strategy, args.epoch_index);
      epoch.equity = args.equity as bigint;
      epoch.netFlow = args.net_flow as bigint;
      epoch.tradesRoot = args.trades_root as string;
      epoch.evidenceUri = args.evidence_uri as string;
      epoch.status = 0;
      epoch.committedAt = event.blockTimestamp ?? null;
      epoch.challenger = null;
      epoch.stake = 0n;
      epoch.pnl = null;
      break;
    }
    case "EpochFinalized": {
      const strategy = requireStrategy(state, args.strategy_id);
      const epoch = epochOf(strategy, args.epoch_index);
      epoch.status = 1;
      epoch.pnl = args.pnl as bigint;
      epoch.equity = args.equity as bigint;
      break;
    }
    case "EpochChallenged": {
      const strategy = requireStrategy(state, args.strategy_id);
      const epoch = epochOf(strategy, args.epoch_index);
      epoch.status = 2;
      epoch.challenger = args.challenger as Address;
      break;
    }
    case "ChallengeResolved": {
      const strategy = requireStrategy(state, args.strategy_id);
      const epoch = epochOf(strategy, args.epoch_index);
      if (args.upheld === true) {
        epoch.status = 3;
      } else {
        epoch.status = 1;
      }
      epoch.challenger = null;
      epoch.stake = 0n;
      break;
    }
    case "StrategyStatusChanged": {
      const strategy = requireStrategy(state, args.strategy_id);
      strategy.status = args.status as number;
      break;
    }
    default:
      // Unknown events are ignored: forward compatibility.
      break;
  }
  if (event.args.strategy_id !== undefined) {
    rederive(requireStrategy(state, event.args.strategy_id));
  }
  state.lastBlock = event.blockNumber > state.lastBlock ? event.blockNumber : state.lastBlock;
  return state;
}

/**
 * Verifies that applying `events` in order is consistent with the reducer's
 * invariants. Used by the transport before committing a backfill batch.
 */
export function invariantsHold(state: CovenantState): boolean {
  for (const s of state.strategies.values()) {
    if (s.owner === ("0x0000000000000000000000000000000000000000" as Address) && s.name === "") {
      return false; // registered strategies must have an owner
    }
    for (const e of s.epochs.values()) {
      if (e.status === 1 && e.pnl === null) return false; // finalized must carry pnl
    }
  }
  return true;
}
