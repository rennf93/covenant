import { createPublicClient, decodeEventLog, http, type Address, type Chain, type Log } from "viem";
import { covenantAbi } from "@covenant/sdk";
import { applyEvent, emptyState, invariantsHold, type DecodedEvent, type CovenantState } from "./state.js";

/**
 * Log transport: polls getLogs for the Covenant contract over block ranges and
 * folds decoded events into a CovenantState. The reducer stays pure; this module
 * is the only RPC-aware piece besides main.ts.
 */

export interface IndexerOptions {
  contract: Address;
  chain: Chain;
  rpcUrl: string;
  /** Blocks per getLogs call. Bounded to respect provider limits. */
  batchSize?: number;
  /** Poll interval in ms for tailing near the head. */
  pollIntervalMs?: number;
}

const MAX_BATCH = 50_000n;

interface ResolvedOptions {
  contract: Address;
  chain: Chain;
  rpcUrl: string;
  batchSize: bigint;
  pollIntervalMs: number;
}

export class Indexer {
  readonly state: CovenantState;
  private readonly client: ReturnType<typeof createPublicClient>;
  private readonly opts: ResolvedOptions;
  private running = false;

  constructor(opts: IndexerOptions) {
    const raw = opts.batchSize ?? 5_000;
    const batch = BigInt(raw);
    this.opts = {
      contract: opts.contract,
      chain: opts.chain,
      rpcUrl: opts.rpcUrl,
      batchSize: batch > MAX_BATCH ? MAX_BATCH : batch > 0n ? batch : 1n,
      pollIntervalMs: opts.pollIntervalMs ?? 2_000,
    };
    this.client = createPublicClient({ chain: opts.chain, transport: http(opts.rpcUrl) });
    this.state = emptyState(opts.contract);
  }

  /** Backfills from `fromBlock` to head, then tails until `stop()` is called. */
  async run(fromBlock: bigint): Promise<void> {
    this.running = true;
    let cursor = fromBlock;
    while (this.running) {
      const head = await this.client.getBlockNumber();
      if (cursor > head) {
        await sleep(this.opts.pollIntervalMs);
        continue;
      }
      const to = head < cursor + this.opts.batchSize - 1n ? head : cursor + this.opts.batchSize - 1n;
      await this.applyRange(cursor, to);
      cursor = to + 1n;
    }
  }

  stop(): void {
    this.running = false;
  }

  /** Fetches and folds one block range. Exposed for tests via a stubbed client. */
  async applyRange(from: bigint, to: bigint): Promise<void> {
    const logs = await this.client.getLogs({
      address: this.opts.contract,
      fromBlock: from,
      toBlock: to,
    });
    const decoded = logs.map(decodeLog).filter((e): e is DecodedEvent => e !== null);
    const next = decoded.reduce(applyEvent, this.state);
    if (!invariantsHold(next)) {
      throw new Error(`indexer invariants violated in range ${from}-${to}`);
    }
  }
}

/** Decodes a raw log against the Covenant ABI; unknown events map to null. */
export function decodeLog(log: Log): DecodedEvent | null {
  try {
    const decoded = decodeEventLog({ abi: covenantAbi, data: log.data, topics: log.topics, strict: false });
    return {
      eventName: decoded.eventName,
      args: decoded.args as Record<string, unknown>,
      blockNumber: log.blockNumber ?? 0n,
    };
  } catch {
    return null;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
