import assert from "node:assert/strict";
import { test } from "node:test";
import {
  encodeAbiParameters,
  getAbiItem,
  keccak256,
  pad,
  toEventSelector,
  toHex,
  type AbiEvent,
  type Address,
  type Chain,
  type Log,
} from "viem";
import { covenantAbi } from "@covenant/sdk";
import { Indexer, type RpcClient } from "../src/transport.js";
import { emptyState, type CovenantState } from "../src/state.js";

const CONTRACT = "0x00000000000000000000000000000000000000c0" as Address;
const OPERATOR = "0x0000000000000000000000000000000000000002" as Address;
const FAKE_CHAIN = { id: 421614, name: "fake" } as unknown as Chain;

/** T0 is a fixed anchor; block n gets timestamp T0 + n. */
const T0 = 1_700_000_000n;

/** Deterministic hash for block n; makeBlocks and staged reorgs both use it. */
function blockHash(n: number): `0x${string}` {
  return keccak256(toHex(`hash-${n}`));
}

interface FakeBlock {
  hash: `0x${string}`;
  timestamp: bigint;
}

interface FakeLog {
  eventName: string;
  args: Record<string, unknown>;
  blockNumber: bigint;
  /** Overrides the derived block hash (staged log conflicts). */
  blockHash?: `0x${string}`;
}

function makeBlocks(numbers: number[]): Map<number, FakeBlock> {
  const blocks = new Map<number, FakeBlock>();
  for (const n of numbers) {
    blocks.set(n, { hash: blockHash(n), timestamp: T0 + BigInt(n) });
  }
  return blocks;
}

/**
 * Encodes a fake event into a wire-shaped log against the real ABI (indexed
 * inputs become topics, the rest is ABI-encoded data), so decodeLog must
 * genuinely decode it.
 */
function rawLog(e: FakeLog): Log {
  const event = getAbiItem({ abi: covenantAbi, name: e.eventName as never }) as AbiEvent;
  const inputs = (event.inputs ?? []) as Array<{ name: string; type: string; indexed?: boolean }>;
  const signature = `${e.eventName}(${inputs.map((i) => i.type).join(",")})`;
  const topics: `0x${string}`[] = [toEventSelector(signature)];
  const dataParams: Array<{ type: string; name: string }> = [];
  const dataValues: unknown[] = [];
  for (const input of inputs) {
    const value = e.args[input.name];
    if (input.indexed) {
      const hex = typeof value === "bigint" ? toHex(value) : (value as `0x${string}`);
      topics.push(pad(hex, { dir: "left", size: 32 }));
    } else {
      dataParams.push({ type: input.type, name: input.name });
      dataValues.push(value);
    }
  }
  const data = dataParams.length > 0 ? encodeAbiParameters(dataParams as never, dataValues as never) : "0x";
  return {
    data,
    topics,
    blockNumber: e.blockNumber,
    blockHash: e.blockHash ?? blockHash(Number(e.blockNumber)),
    transactionHash: keccak256(toHex(`tx-${e.blockNumber}`)),
    transactionIndex: 0,
    logIndex: 0,
    address: CONTRACT,
    removed: false,
  } as unknown as Log;
}

/**
 * Deterministic fake RPC: blocks by number, logs scheduled per block. Tests
 * mutate `blocks`/`logs` between batches to stage reorgs.
 */
function makeFakeRpc(opts: { head: bigint; blocks: Map<number, FakeBlock>; logs: FakeLog[] }) {
  const calls = { getBlock: 0, getLogs: 0 };
  const client: RpcClient = {
    getBlockNumber: async () => opts.head,
    getLogs: async ({ fromBlock, toBlock }) => {
      calls.getLogs += 1;
      return opts.logs
        .filter((l) => l.blockNumber >= fromBlock && l.blockNumber <= toBlock)
        .map(rawLog);
    },
    getBlock: async ({ blockNumber }) => {
      calls.getBlock += 1;
      const block = opts.blocks.get(Number(blockNumber));
      if (!block) throw new Error(`block ${blockNumber} not found`);
      return { number: blockNumber, hash: block.hash, timestamp: block.timestamp };
    },
  };
  return { client, calls };
}

function registered(strategyId: bigint, blockNumber: bigint): FakeLog {
  return {
    eventName: "StrategyRegistered",
    args: { strategy_id: strategyId, owner: OPERATOR, name: "fake", bond: 10n },
    blockNumber,
  };
}

function committedArgs(equity: bigint): Record<string, unknown> {
  return {
    strategy_id: 1n,
    epoch_index: 0n,
    equity,
    net_flow: 1000n,
    trades_root: keccak256(toHex("r")),
    evidence_uri: "ipfs://e",
  };
}

function makeIndexer(client: RpcClient, onBatch?: (s: CovenantState) => void): Indexer {
  return new Indexer({
    contract: CONTRACT,
    chain: FAKE_CHAIN,
    rpcUrl: "http://fake",
    initialState: emptyState(CONTRACT),
    onBatch,
    retry: { attempts: 3, baseDelayMs: 1 },
    pollIntervalMs: 1,
    client,
  });
}

test("batches fold with block timestamps as ISO committedAt/createdAt", async () => {
  const blocks = makeBlocks([5, 6]);
  const logs: FakeLog[] = [
    registered(1n, 5n),
    { eventName: "EpochCommitted", args: committedArgs(1000n), blockNumber: 6n },
  ];
  const { client } = makeFakeRpc({ head: 6n, blocks, logs });
  const indexer = makeIndexer(client);

  await indexer.applyRange(1n, 6n);
  const s = indexer.state.strategies.get("1")!;
  assert.equal(s.createdAt, new Date(Number(T0 + 5n) * 1000).toISOString());
  assert.equal(s.epochs.get("0")!.committedAt, new Date(Number(T0 + 6n) * 1000).toISOString());
});

test("block metadata is cached across batches (one getBlock per unique block)", async () => {
  const blocks = makeBlocks([5, 10, 15]);
  const logs: FakeLog[] = [registered(1n, 5n), registered(2n, 10n)];
  const { client, calls } = makeFakeRpc({ head: 15n, blocks, logs });
  const indexer = makeIndexer(client);

  await indexer.applyRange(1n, 15n);
  const first = calls.getBlock;
  assert.ok(first >= 3); // unique log blocks 5 and 10, plus the range anchor 15
  // an overlapping range pays exactly one fresh anchor check; everything else
  // comes from the cache
  await indexer.applyRange(6n, 15n);
  assert.equal(calls.getBlock, first + 1);
});

test("a reorged head rolls back to the last snapshot and re-folds", async () => {
  const blocks = makeBlocks([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  const logs: FakeLog[] = [
    registered(1n, 2n),
    { eventName: "EpochCommitted", args: committedArgs(7n), blockNumber: 8n },
  ];
  const { client, calls } = makeFakeRpc({ head: 10n, blocks, logs });
  const batches: string[] = [];
  const indexer = makeIndexer(client, (s) => batches.push(s.lastBlock.toString()));

  await indexer.applyRange(1n, 5n);
  await indexer.applyRange(6n, 10n);
  assert.equal(indexer.state.strategies.get("1")!.epochs.get("0")!.equity, 7n);

  // REORG: block 8 onward is replaced and the epoch now claims 9 instead of 7.
  for (let n = 8; n <= 10; n++) {
    blocks.set(n, { hash: keccak256(toHex(`reorged-${n}`)), timestamp: T0 + BigInt(n) });
  }
  logs.splice(1, 1, { eventName: "EpochCommitted", args: committedArgs(9n), blockNumber: 8n });

  // Batch 3's continuity check sees the remembered anchor hash change, rolls
  // back to the batch-1 snapshot, and reports where to re-fold from.
  const result = await indexer.applyRange(11n, 15n);
  assert.equal(result.rollback, true);
  assert.equal(result.cursor, 6n);
  const s = indexer.state.strategies.get("1")!;
  assert.equal(s.epochs.size, 0, "state is exactly the batch-1 snapshot again");

  // Re-folding the reorged range heals the state to the new chain.
  await indexer.applyRange(6n, 15n);
  assert.equal(indexer.state.strategies.get("1")!.epochs.get("0")!.equity, 9n);
  assert.ok(calls.getLogs >= 3, "the reorged range was re-fetched");
});

test("a reorg deeper than the surviving snapshots resets and re-folds", async () => {
  const blocks = makeBlocks([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  const logs: FakeLog[] = [registered(1n, 2n)];
  const { client } = makeFakeRpc({ head: 10n, blocks, logs });
  const indexer = makeIndexer(client);

  await indexer.applyRange(1n, 5n);
  await indexer.applyRange(6n, 10n);
  // REORG replaces block 2 and every descendant (descendants always change
  // with their parent), so both snapshots are poisoned in turn.
  for (let n = 2; n <= 10; n++) {
    blocks.set(n, { hash: keccak256(toHex(`reorged-${n}`)), timestamp: T0 + BigInt(n) });
  }
  logs.splice(0, 1, {
    eventName: "StrategyRegistered",
    args: { strategy_id: 1n, owner: OPERATOR, name: "reorged", bond: 11n },
    blockNumber: 2n,
  });

  // First rollback lands on the batch-1 snapshot; its anchor is poisoned too,
  // so the second rollback runs out of snapshots and resets to genesis.
  const first = await indexer.applyRange(11n, 15n);
  assert.equal(first.rollback, true);
  assert.equal(first.cursor, 6n);
  const second = await indexer.applyRange(6n, 15n);
  assert.equal(second.rollback, true);
  assert.equal(second.cursor, 0n);
  assert.equal(indexer.state.strategies.size, 0);

  await indexer.applyRange(1n, 15n);
  assert.equal(indexer.state.strategies.get("1")!.name, "reorged");
});

test("a fetched log whose block hash breaks continuity rolls back", async () => {
  const blocks = makeBlocks([5]);
  const logs: FakeLog[] = [registered(1n, 5n)];
  const { client } = makeFakeRpc({ head: 6n, blocks, logs });
  const indexer = makeIndexer(client);
  await indexer.applyRange(1n, 5n);

  // The chain serves the same block's log with a different hash: the per-log
  // check pins the conflict regardless of the anchor.
  logs.push({ ...registered(1n, 5n), blockHash: keccak256(toHex("forged")) });
  const result = await indexer.applyRange(1n, 6n);
  assert.equal(result.rollback, true);
  assert.equal(indexer.state.strategies.size, 0, "conflicting history is discarded");
});

test("RPC errors retry with exponential backoff before giving up", async () => {
  const blocks = makeBlocks([5]);
  let failures = 0;
  const flaky: RpcClient = {
    getBlockNumber: async () => 5n,
    getLogs: async () => {
      failures += 1;
      if (failures < 3) throw new Error("429 too many requests");
      return [rawLog(registered(1n, 5n))];
    },
    getBlock: makeFakeRpc({ head: 5n, blocks, logs: [] }).client.getBlock,
  };
  const indexer = makeIndexer(flaky);
  await indexer.applyRange(1n, 5n);
  assert.equal(failures, 3, "two failures retried, third attempt succeeded");
  assert.ok(indexer.state.strategies.get("1") !== null);

  const hopeless: RpcClient = {
    getBlockNumber: flaky.getBlockNumber,
    getLogs: async () => {
      throw new Error("down");
    },
    getBlock: flaky.getBlock,
  };
  const failing = makeIndexer(hopeless);
  await assert.rejects(failing.applyRange(1n, 5n), /RPC getLogs failed after 3 attempts/);
});

test("a batch that keeps failing does not kill the run loop", async () => {
  const blocks = makeBlocks([5]);
  const { client } = makeFakeRpc({ head: 5n, blocks, logs: [] });
  const alwaysFails: RpcClient = {
    getBlockNumber: async () => {
      throw new Error("head unreachable");
    },
    getLogs: client.getLogs,
    getBlock: client.getBlock,
  };
  const indexer = makeIndexer(alwaysFails);
  // three bounded iterations that all fail: run returns, still resumable
  await indexer.run(1n, 3);
  await assert.doesNotReject(indexer.run(1n, 1));
});

test("a head below the cursor just polls (nothing to index yet)", async () => {
  const blocks = makeBlocks([3]);
  const { client } = makeFakeRpc({ head: 3n, blocks, logs: [] });
  const indexer = makeIndexer(client);
  await indexer.run(5n, 2);
  assert.equal(indexer.state.lastBlock, 0n);
});

test("invariants violations are thrown before state is replaced", async () => {
  // ChallengeResolved with upheld=false finalizes an epoch that never
  // committed: status 1 without pnl -> the candidate is rejected wholesale.
  const blocks = makeBlocks([5]);
  const logs: FakeLog[] = [
    registered(1n, 5n),
    {
      eventName: "ChallengeResolved",
      args: { strategy_id: 1n, epoch_index: 0n, upheld: false, forced: false, resolver: OPERATOR },
      blockNumber: 5n,
    },
  ];
  const { client } = makeFakeRpc({ head: 5n, blocks, logs });
  const indexer = makeIndexer(client);
  await assert.rejects(indexer.applyRange(1n, 5n), /invariants violated/);
  assert.equal(indexer.state.strategies.size, 0, "live state was never replaced");
});
