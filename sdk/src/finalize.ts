import { CheckpointStatus } from "./client.js";

/**
 * Finalizer discovery: which pending checkpoints may be finalized right now.
 * Pure and time-injected so the decision is unit-testable without a chain.
 */

/** A committed checkpoint as the finalizer sees it. */
export interface CheckpointTiming {
  strategyId: bigint;
  epochIndex: bigint;
  status: number;
  /** Unix seconds the checkpoint was committed. */
  committedAt: bigint;
}

export interface DueCheckpoint {
  strategyId: bigint;
  epochIndex: bigint;
  committedAt: bigint;
  /** Unix second the challenge window closed and finalize became callable. */
  finalizableAt: bigint;
}

/**
 * Pending checkpoints whose challenge window has fully elapsed, sorted by
 * (strategyId, epochIndex) so runs are deterministic and the printed plan is
 * stable. Finalized/challenged/invalidated epochs never appear.
 */
export function findDueCheckpoints(args: {
  nowSec: bigint;
  windowSec: bigint;
  checkpoints: readonly CheckpointTiming[];
}): DueCheckpoint[] {
  return args.checkpoints
    .filter((c) => c.status === CheckpointStatus.Pending)
    .map((c) => ({
      strategyId: c.strategyId,
      epochIndex: c.epochIndex,
      committedAt: c.committedAt,
      finalizableAt: c.committedAt + args.windowSec,
    }))
    .filter((c) => c.finalizableAt <= args.nowSec)
    .sort((a, b) => {
      if (a.strategyId !== b.strategyId) return a.strategyId < b.strategyId ? -1 : 1;
      if (a.epochIndex !== b.epochIndex) return a.epochIndex < b.epochIndex ? -1 : 1;
      return 0;
    });
}
