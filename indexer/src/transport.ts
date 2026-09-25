import { createPublicClient, decodeEventLog, http, type Address, type Chain, type Log } from "viem";
import { covenantAbi } from "@covenant/sdk";
import {
  applyEvent,
  emptyState,
  invariantsHold,
  type CovenantState,
  type DecodedEvent,
} from "./state.js";

/**
 * Log transport: polls getLogs for the Covenant contract over block ranges and
 * folds decoded events into a CovenantState. The reducer stays pure; this module
 * is the only RPC-aware piece besides main.ts.
 *
 * Resilience contract:
 *  - every RPC call retries with exponential backoff, and a batch that still
 *    fails leaves the process alive (the run loop logs and keeps polling);
 *  - the last K=2 processed block hashes are remembered and re-checked against
 *    the chain each batch; a mismatch (or a fetched log whose block hash
 *    breaks continuity) rolls back to the last consistent snapshot and re-folds;
 *  - each committed batch deep-clones into a snapshot ring, so rollback is
 *    exact state, not a guess.
 */

export interface IndexerOptions {
  contract: Address;
  chain: Chain;
  rpcUrl: string;
  /** Blocks per getLogs call. Bounded to respect provider limits. */
  batchSize?: number;
  /** Poll interval in ms for tailing near the head. */
  pollIntervalMs?: number;
  /** Preloaded state (restored persistence); defaults to empty. */
  initialState?: CovenantState;
  /** Called after every committed batch (persistence, SSE fan-out). */
  onBatch?: (state: CovenantState) => void;
  /** RPC retry policy. */
  retry?: { attempts?: number; baseDelayMs?: number };
  /** Test seam: replaces the viem public client. */
  client?: RpcClient;
}

/** The RPC surface the indexer needs; injectable for tests. */
export interface RpcClient {
  getBlockNumber(): Promise<bigint>;
  getLogs(args: { address: Address; fromBlock: bigint; toBlock: bigint }): Promise<Log[]>;
  getBlock(args: { blockNumber: bigint }): Promise<{
    number: bigint;
    hash: `0x${string}`;
    timestamp: bigint;
  }>;
}

const MAX_BATCH = 50_000n;
/** Processed block hashes kept for reorg detection. */
const REORG_LOOKBACK = 2;
/** Deep-clone snapshots kept for rollback (batch granularity). */
const SNAPSHOT_KEEP = 4;
const DEFAULT_ATTEMPTS = 6;
const DEFAULT_BASE_DELAY_MS = 250;
const MAX_BACKOFF_MS = 30_000;

interface ResolvedOptions {
  contract: Address;
  chain: Chain;
  rpcUrl: string;
  batchSize: bigint;
  pollIntervalMs: number;
  onBatch?: (state: CovenantState) => void;
  attempts: number;
  baseDelayMs: number;
}

interface BlockMeta {
  hash: `0x${string}`;
  timestamp: bigint;
}

interface Snapshot {
  state: CovenantState;
  lastBlock: bigint;
  blockHashes: Map<string, `0x${string}`>;
}

export interface BatchResult {
  /** True when a reorg was detected and state rolled back; re-run from `cursor`. */
  rollback: boolean;
  /** Next block to fetch (only meaningful with rollback). */
  cursor?: bigint;
}

export class Indexer {
  state: CovenantState;
  private readonly client: RpcClient;
  private readonly opts: ResolvedOptions;
  private running = false;
  /** Block number (string key) -> hash, last REORG_LOOKBACK processed blocks. */
  private blockHashes = new Map<string, `0x${string}`>();
  /** Block metadata cache: one getBlock per unique block, reused across batches. */
  private blockCache = new Map<string, BlockMeta>();
  private snapshots: Snapshot[] = [];
  /** Where the current run started; a too-deep rollback restarts from here. */
  private origin: bigint | null = null;

  constructor(opts: IndexerOptions) {
    const raw = opts.batchSize ?? 5_000;
    const batch = BigInt(raw);
    this.opts = {
      contract: opts.contract,
      chain: opts.chain,
      rpcUrl: opts.rpcUrl,
      batchSize: batch > MAX_BATCH ? MAX_BATCH : batch > 0n ? batch : 1n,
      pollIntervalMs: opts.pollIntervalMs ?? 2_000,
      onBatch: opts.onBatch,
      attempts: opts.retry?.attempts ?? DEFAULT_ATTEMPTS,
      baseDelayMs: opts.retry?.baseDelayMs ?? DEFAULT_BASE_DELAY_MS,
    };
    this.client = opts.client ?? (createPublicClient({ chain: opts.chain, transport: http(opts.rpcUrl) }) as unknown as RpcClient);
    this.state = opts.initialState ?? emptyState(opts.contract);
  }

  /**
   * Backfills from `fromBlock` to head, then tails until `stop()` is called.
   * `batches` bounds the number of poll-loop iterations (test seam); a batch
   * that detects a reorg rolls back and re-folds within the same budget.
   */
  async run(fromBlock: bigint, batches?: number): Promise<void> {
    this.running = true;
    if (this.origin === null) this.origin = fromBlock;
    let cursor = fromBlock;
    while (this.running) {
      if (batches !== undefined) {
        if (batches <= 0) return;
        batches -= 1;
      }
      try {
        const head = await this.withRetry("getBlockNumber", () => this.client.getBlockNumber());
        if (cursor > head) {
          await sleep(this.opts.pollIntervalMs);
          continue;
        }
        const to = head < cursor + this.opts.batchSize - 1n ? head : cursor + this.opts.batchSize - 1n;
        const result = await this.applyRange(cursor, to);
        if (result.rollback) {
          cursor = result.cursor ?? fromBlock;
          continue;
        }
        cursor = to + 1n;
      } catch (e) {
        // A batch that still fails after retries must not kill the process:
        // log, wait, and keep polling; the next batch resumes from the cursor.
        console.error(`indexer batch failed, will retry: ${e instanceof Error ? e.message : String(e)}`);
        await sleep(this.opts.pollIntervalMs);
      }
    }
  }

  stop(): void {
    this.running = false;
  }

  /**
   * Fetches and folds one block range. Exposed for tests via a stubbed client.
   * On success the batch is committed atomically: the folded candidate state is
   * validated against the reducer invariants BEFORE replacing live state, so a
   * violating range never corrupts what the API is serving.
   */
  async applyRange(from: bigint, to: bigint): Promise<BatchResult> {
    // Continuity check: the newest remembered block must still hash the same.
    const anchor = this.newestRemembered();
    if (anchor !== null) {
      const fresh = await this.blockMeta(anchor.number, true);
      if (fresh && fresh.hash !== anchor.hash) {
        return this.rollback(anchor.number);
      }
    }

    const logs = await this.withRetry("getLogs", () =>
      this.client.getLogs({ address: this.opts.contract, fromBlock: from, toBlock: to }),
    );
    const decoded = logs.map(decodeLog).filter((e): e is DecodedEvent => e !== null);

    // Defense in depth: a log overlapping remembered blocks with a different
    // hash pins the exact block the reorg starts at.
    for (const e of decoded) {
      if (e.blockHash === undefined) continue;
      const remembered = this.blockHashes.get(e.blockNumber.toString());
      if (remembered !== undefined && remembered !== e.blockHash) {
        return this.rollback(e.blockNumber);
      }
    }

    // Timestamps + hashes: one getBlock per unique block, served from cache.
    const numbers = new Set<bigint>(decoded.map((e) => e.blockNumber));
    numbers.add(to); // anchor even ranges without logs so continuity survives
    for (const n of numbers) await this.blockMeta(n, false);

    const enriched = decoded.map((e) => ({
      ...e,
      blockTimestamp: this.blockCache.get(e.blockNumber.toString())?.timestamp,
    }));
    const candidate = enriched.reduce(applyEvent, structuredClone(this.state));
    if (!invariantsHold(candidate)) {
      throw new Error(`indexer invariants violated in range ${from}-${to}`);
    }
    this.state = candidate;

    const metas: Array<{ number: bigint; meta: BlockMeta }> = [];
    for (const n of numbers) {
      const meta = this.blockCache.get(n.toString());
      if (meta) metas.push({ number: n, meta });
    }
    this.rememberHashes(metas);
    this.snapshots.push({
      state: structuredClone(this.state),
      lastBlock: to,
      blockHashes: new Map(this.blockHashes),
    });
    if (this.snapshots.length > SNAPSHOT_KEEP) this.snapshots.shift();

    this.opts.onBatch?.(this.state);
    return { rollback: false };
  }

  /**
   * Restores the newest snapshot taken strictly before `conflictBlock` and
   * returns the block to re-fold from. Snapshots at or after the conflict are
   * poisoned (they describe the reorged-away chain) and dropped; with no valid
   * snapshot left the state resets and re-folds from the run's origin.
   */
  private rollback(conflictBlock: bigint): BatchResult {
    let snap: Snapshot | null = null;
    while (this.snapshots.length > 0) {
      const top = this.snapshots[this.snapshots.length - 1]!;
      if (top.lastBlock < conflictBlock) {
        snap = top;
        break;
      }
      this.snapshots.pop();
    }
    if (snap !== null) {
      this.state = structuredClone(snap.state);
      this.blockHashes = new Map(snap.blockHashes);
      this.blockCache.clear(); // cached timestamps/hashes may describe the dead chain
      return { rollback: true, cursor: snap.lastBlock + 1n };
    }
    this.state = emptyState(this.opts.contract);
    this.blockHashes = new Map();
    this.blockCache.clear();
    return { rollback: true, cursor: this.origin ?? 0n };
  }

  private newestRemembered(): { number: bigint; hash: `0x${string}` } | null {
    let best: { number: bigint; hash: `0x${string}` } | null = null;
    for (const [key, hash] of this.blockHashes) {
      const n = BigInt(key);
      if (best === null || n > best.number) best = { number: n, hash };
    }
    return best;
  }

  /** Keeps only the last REORG_LOOKBACK block hashes; older ones cannot detect anything. */
  private rememberHashes(metas: Array<{ number: bigint; meta: BlockMeta }>): void {
    const merged = new Map(this.blockHashes);
    for (const { number, meta } of metas) merged.set(number.toString(), meta.hash);
    const sorted = [...merged.keys()].map(BigInt).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
    this.blockHashes = new Map(
      sorted.slice(-REORG_LOOKBACK).map((n) => [n.toString(), merged.get(n.toString())!]),
    );
  }

  /**
   * Block hash + timestamp for `number`, cached. `fresh` bypasses the cache:
   * the continuity check must see the chain, not our memory of it. Returns
   * null when the block is unavailable (pruned or raced with a reorg); the
   * batch proceeds without timestamps rather than stalling.
   */
  private async blockMeta(number: bigint, fresh: boolean): Promise<BlockMeta | null> {
    const key = number.toString();
    if (!fresh) {
      const hit = this.blockCache.get(key);
      if (hit) return hit;
    } else {
      this.blockCache.delete(key);
    }
    try {
      const block = await this.withRetry("getBlock", () => this.client.getBlock({ blockNumber: number }));
      const meta: BlockMeta = { hash: block.hash, timestamp: block.timestamp };
      if (this.blockCache.size >= 1024) {
        const oldest = this.blockCache.keys().next().value;
        if (oldest !== undefined) this.blockCache.delete(oldest);
      }
      this.blockCache.set(key, meta);
      return meta;
    } catch {
      return null;
    }
  }

  /** Retries an RPC call with exponential backoff; throws only after `attempts`. */
  private async withRetry<T>(label: string, fn: () => Promise<T>): Promise<T> {
    let lastError: unknown;
    for (let attempt = 0; attempt < this.opts.attempts; attempt++) {
      try {
        return await fn();
      } catch (e) {
        lastError = e;
        if (attempt < this.opts.attempts - 1) {
          await sleep(Math.min(MAX_BACKOFF_MS, this.opts.baseDelayMs * 2 ** attempt));
        }
      }
    }
    throw new Error(`RPC ${label} failed after ${this.opts.attempts} attempts: ${String(lastError)}`);
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
      blockHash: log.blockHash ?? undefined,
    };
  } catch {
    return null;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
