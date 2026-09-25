import assert from "node:assert/strict";
import { test } from "node:test";
import type { Server } from "node:http";
import { keccak256, toHex, type Address } from "viem";
import { applyEvent, emptyState, type CovenantState, type DecodedEvent } from "../src/state.js";
import { leaderboardRow, sparkSeries, startApi } from "../src/server.js";

const CONTRACT = "0x00000000000000000000000000000000000000c0" as Address;
const OPERATOR = "0x0000000000000000000000000000000000000002" as Address;

function emit(name: string, args: Record<string, unknown>, blockNumber = 1n): DecodedEvent {
  return { eventName: name, args, blockNumber };
}

function makeState(): CovenantState {
  return emptyState(CONTRACT);
}

/** Registers strategy 1 and commits `count` epochs, finalizing `finalize` of them. */
function seedHistory(state: CovenantState, count: number, finalize: number, seedEquity = 1000n): CovenantState {
  let s = applyEvent(state, emit("StrategyRegistered", { strategy_id: 1n, owner: OPERATOR, name: "spark", bond: 10n }));
  for (let i = 0; i < count; i++) {
    const equity = seedEquity + BigInt(i) * 100_000n; // +0.1 USDG per epoch (6 decimals)
    s = applyEvent(s, emit("EpochCommitted", { strategy_id: 1n, epoch_index: BigInt(i), equity, net_flow: 0n, trades_root: keccak256(toHex(`r${i}`)), evidence_uri: "" }));
    if (i < finalize) {
      s = applyEvent(s, emit("EpochFinalized", { strategy_id: 1n, epoch_index: BigInt(i), pnl: 0n, equity }));
    }
  }
  return s;
}

test("spark is null below 2 finalized epochs", () => {
  const one = sparkSeries(seedHistory(makeState(), 3, 1).strategies.get("1")!);
  assert.equal(one, null);
  const none = sparkSeries(seedHistory(makeState(), 3, 0).strategies.get("1")!);
  assert.equal(none, null);
});

test("spark converts base units to human numbers in ascending epoch order", () => {
  const s = seedHistory(makeState(), 4, 4, 1_000_000n);
  const spark = sparkSeries(s.strategies.get("1")!);
  assert.deepEqual(spark, [1, 1.1, 1.2, 1.3]);
  const row = leaderboardRow(s.strategies.get("1")!);
  assert.deepEqual(row.spark, [1, 1.1, 1.2, 1.3]);
});

test("spark skips pending and challenged epochs entirely", () => {
  let s = seedHistory(makeState(), 3, 3, 1_000_000n);
  // epoch 1 gets challenged after finalization: it must drop out of the series
  s = applyEvent(s, emit("EpochChallenged", { strategy_id: 1n, epoch_index: 1n, challenger: OPERATOR }));
  assert.deepEqual(sparkSeries(s.strategies.get("1")!), [1, 1.2]);
});

test("spark caps to the last 32 finalized points", () => {
  const spark = sparkSeries(seedHistory(makeState(), 40, 40, 1_000_000n).strategies.get("1")!);
  assert.ok(spark !== null);
  assert.equal(spark.length, 32);
  // 40 finalized points, capped to the last 32: first kept epoch is index 8
  assert.equal(spark[0], 1.8);
  assert.equal(spark[spark.length - 1], 4.9);
});

/** Resolves the ephemeral port once the server is actually listening. */
async function ephemeralPort(server: Server): Promise<number> {
  return await new Promise((resolve, reject) => {
    server.once("listening", () => {
      const addr = server.address();
      if (addr !== null && typeof addr === "object") resolve(addr.port);
      else reject(new Error("server has no address"));
    });
    server.once("error", reject);
  });
}

interface RowDto {
  id: string;
  spark: number[] | null;
  derived: { returnWad: string | null };
  epochs?: Array<{ epochIndex: string; equity: string | null }>;
}

test("api serves health, leaderboard, detail, and 404s", async () => {
  let state = seedHistory(makeState(), 2, 2, 1_000_000n);
  // a registered strategy without epochs sorts last and carries a null spark
  state = applyEvent(state, emit("StrategyRegistered", { strategy_id: 2n, owner: OPERATOR, name: "empty", bond: 5n }, 2n));
  const server = startApi(state, 0);
  try {
    const base = `http://127.0.0.1:${await ephemeralPort(server)}`;

    const health = (await (await fetch(`${base}/health`)).json()) as { ok: boolean; lastBlock: string };
    assert.equal(health.ok, true);
    assert.ok(BigInt(health.lastBlock) >= 2n);

    const board = (await (await fetch(`${base}/strategies`)).json()) as { rows: RowDto[] };
    assert.equal(board.rows.length, 2);
    // best return first: the strategy with history leads the empty one
    assert.equal(board.rows[0]?.id, "1");
    assert.deepEqual(board.rows[0]?.spark, [1, 1.1]);
    assert.equal(board.rows[1]?.id, "2");
    assert.equal(board.rows[1]?.spark, null);
    assert.equal(board.rows[1]?.derived.returnWad, null);

    const detail = (await (await fetch(`${base}/strategies/1`)).json()) as RowDto;
    assert.equal(detail.epochs?.length, 2);
    assert.equal(detail.epochs?.[0]?.epochIndex, "0");
    assert.equal(detail.epochs?.[0]?.equity, "1000000");

    const unknown = await fetch(`${base}/strategies/99`);
    assert.equal(unknown.status, 404);
    assert.deepEqual(await unknown.json(), { error: "unknown strategy" });

    const missing = await fetch(`${base}/nope`);
    assert.equal(missing.status, 404);
    assert.deepEqual(await missing.json(), { error: "not found" });
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
