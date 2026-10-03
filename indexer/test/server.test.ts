import assert from "node:assert/strict";
import { test } from "node:test";
import type { Server } from "node:http";
import { keccak256, toHex, type Address } from "viem";
import { applyEvent, emptyState, type CovenantState, type DecodedEvent } from "../src/state.js";
import {
  createHub,
  leaderboardRow,
  sparkSeries,
  startApi,
  windowReturn,
  windowSeconds,
} from "../src/server.js";

const CONTRACT = "0x00000000000000000000000000000000000000c0" as Address;
const OPERATOR = "0x0000000000000000000000000000000000000002" as Address;

const NOW = BigInt(Math.floor(Date.now() / 1000));
const DAY = 86_400n;

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

    assert.equal(missing.headers.get("access-control-allow-origin"), "*", "CORS on every route");
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("api detail epochs carry ISO committedAt plus challenger and stake", async () => {
  let state = makeState();
  state = applyEvent(state, emit("StrategyRegistered", { strategy_id: 1n, owner: OPERATOR, name: "iso", bond: 10n }, 2n));
  state = applyEvent(
    state,
    emit("EpochCommitted", { strategy_id: 1n, epoch_index: 0n, equity: 1_000_000n, net_flow: 0n, trades_root: keccak256(toHex("r")), evidence_uri: "ipfs://e" }, 3n),
  );
  const server = startApi(state, 0);
  try {
    const base = `http://127.0.0.1:${await ephemeralPort(server)}`;
    const detail = (await (await fetch(`${base}/strategies/1`)).json()) as {
      epochs: Array<{ committedAt: string | null; challenger: string | null; stake: string }>;
    };
    assert.equal(detail.epochs[0]!.committedAt, null); // no block timestamp given
    assert.equal(detail.epochs[0]!.challenger, null);
    assert.equal(detail.epochs[0]!.stake, "0");
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("windowSeconds parses Nd forms, all, and rejects the rest", () => {
  assert.equal(windowSeconds("7d"), 7 * 86_400);
  assert.equal(windowSeconds("30d"), 30 * 86_400);
  assert.equal(windowSeconds("all"), null);
  assert.equal(windowSeconds("1h"), undefined);
  assert.equal(windowSeconds("d"), undefined);
});

/** Strategy 1: one old finalized epoch (+10%), one fresh (+20%). Strategy 2: fresh only. */
function seedWindows(): CovenantState {
  let state = makeState();
  state = applyEvent(state, emit("StrategyRegistered", { strategy_id: 1n, owner: OPERATOR, name: "old-fresh", bond: 10n }));
  state = applyEvent(state, emit("EpochCommitted", { strategy_id: 1n, epoch_index: 0n, equity: 1_000_000n, net_flow: 0n, trades_root: keccak256(toHex("a")), evidence_uri: "" }));
  state = applyEvent(state, emit("EpochFinalized", { strategy_id: 1n, epoch_index: 0n, pnl: 100_000n, equity: 1_100_000n }, 1n));
  state.strategies.get("1")!.epochs.get("0")!.finalizedAt = NOW - 30n * DAY;
  state = applyEvent(state, emit("EpochCommitted", { strategy_id: 1n, epoch_index: 1n, equity: 1_200_000n, net_flow: 0n, trades_root: keccak256(toHex("b")), evidence_uri: "" }));
  state = applyEvent(state, emit("EpochFinalized", { strategy_id: 1n, epoch_index: 1n, pnl: 200_000n, equity: 1_400_000n }, 2n));
  state.strategies.get("1")!.epochs.get("1")!.finalizedAt = NOW - DAY;

  state = applyEvent(state, emit("StrategyRegistered", { strategy_id: 2n, owner: OPERATOR, name: "fresh-only", bond: 10n }));
  state = applyEvent(state, emit("EpochCommitted", { strategy_id: 2n, epoch_index: 0n, equity: 1_000_000n, net_flow: 0n, trades_root: keccak256(toHex("c")), evidence_uri: "" }));
  state = applyEvent(state, emit("EpochFinalized", { strategy_id: 2n, epoch_index: 0n, pnl: 500_000n, equity: 1_500_000n }, 3n));
  state.strategies.get("2")!.epochs.get("0")!.finalizedAt = NOW - DAY;
  return state;
}

test("window return is time-weighted over finalizedAt, null outside the window", () => {
  const state = seedWindows();
  const s1 = state.strategies.get("1")!;
  const s2 = state.strategies.get("2")!;

  // all-time: sum(pnl) over the first finalized equity, 1e18 fixed point
  assert.equal(windowReturn(s1, null), (300_000n * 10n ** 18n) / 1_100_000n);
  // 7d window drops the 30-day-old epoch: its pnl over its own first-in-window equity
  assert.equal(windowReturn(s1, NOW - 7n * DAY), (200_000n * 10n ** 18n) / 1_400_000n);
  // a cutoff later than every finalize time leaves nothing: null, never zero
  assert.equal(windowReturn(s2, NOW + DAY), null);
  // an epoch without a known finalize time never counts toward a time window
  const unknown = structuredClone(s2);
  unknown.epochs.get("0")!.finalizedAt = null;
  assert.equal(windowReturn(unknown, NOW - DAY), null);
});

test("/strategies sorts by windowed return with nulls last, and paginates", async () => {
  // strategy 3 has high all-time return but nothing fresh: last in a 7d window
  let state = seedWindows();
  state = applyEvent(state, emit("StrategyRegistered", { strategy_id: 3n, owner: OPERATOR, name: "stale-star", bond: 10n }));
  state = applyEvent(state, emit("EpochCommitted", { strategy_id: 3n, epoch_index: 0n, equity: 1_000_000n, net_flow: 0n, trades_root: keccak256(toHex("d")), evidence_uri: "" }));
  state = applyEvent(state, emit("EpochFinalized", { strategy_id: 3n, epoch_index: 0n, pnl: 900_000n, equity: 1_900_000n }, 4n));
  state.strategies.get("3")!.epochs.get("0")!.finalizedAt = NOW - 30n * DAY;

  const server = startApi(state, 0);
  try {
    const base = `http://127.0.0.1:${await ephemeralPort(server)}`;

    const week = (await (await fetch(`${base}/strategies?window=7d`)).json()) as {
      rows: Array<{ id: string; windowReturnWad: string | null }>;
      pagination: { total: number; limit: number; offset: number };
    };
    assert.deepEqual(
      week.rows.map((r) => r.id),
      ["2", "1", "3"], // fresh-only beats fresh+old; stale-star sorts last
    );
    assert.ok(BigInt(week.rows[0]!.windowReturnWad!) > BigInt(week.rows[1]!.windowReturnWad!));
    assert.equal(week.rows[2]!.windowReturnWad, null);
    assert.deepEqual(week.pagination, { total: 3, limit: 50, offset: 0 });

    const paged = (await (await fetch(`${base}/strategies?window=7d&limit=1&offset=1`)).json()) as {
      rows: Array<{ id: string }>;
      pagination: { total: number; limit: number; offset: number };
    };
    assert.deepEqual(paged.rows.map((r) => r.id), ["1"]);
    assert.deepEqual(paged.pagination, { total: 3, limit: 1, offset: 1 });

    // default window is all-time: stale-star's 90% leads
    const allTime = (await (await fetch(`${base}/strategies`)).json()) as { rows: Array<{ id: string }> };
    assert.equal(allTime.rows[0]!.id, "3");

    const bad = await fetch(`${base}/strategies?window=1h`);
    assert.equal(bad.status, 400);
    assert.deepEqual(await bad.json(), { error: "invalid window; use 7d, 30d, or all" });
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("/stream pushes a snapshot on connect and on every hub emit", async () => {
  const state = seedWindows();
  const hub = createHub();
  const server = startApi(state, 0, hub);
  const controller = new AbortController();
  try {
    const base = `http://127.0.0.1:${await ephemeralPort(server)}`;
    const res = await fetch(`${base}/stream`, { signal: controller.signal });
    assert.equal(res.headers.get("content-type"), "text/event-stream");
    assert.equal(res.headers.get("access-control-allow-origin"), "*");
    assert.ok(res.body !== null);

    const reader = res.body.getReader();
    const chunks: string[] = [];
    const readChunk = async (): Promise<string> => {
      const { value, done } = await reader.read();
      assert.ok(!done);
      chunks.push(new TextDecoder().decode(value));
      return chunks[chunks.length - 1]!;
    };

    // connect = immediate full snapshot
    const first = await readChunk();
    assert.match(first, /^event: snapshot\ndata: /);
    const payload = JSON.parse(first.slice(first.indexOf("data: ") + 6)) as { rows: Array<{ id: string }> };
    assert.deepEqual(payload.rows.map((r) => r.id), ["2", "1"]);

    // every committed batch hub.emit()s; the subscriber gets a fresh snapshot
    hub.emit();
    const second = await readChunk();
    assert.match(second, /^event: snapshot\ndata: /);
  } finally {
    controller.abort();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("leaderboardRow carries the windowed return alongside the all-time one", () => {
  const state = seedWindows();
  const s1 = state.strategies.get("1")!;
  const week = leaderboardRow(s1, windowReturn(s1, NOW - 7n * DAY));
  assert.notEqual(week.windowReturnWad, week.derived.returnWad);
  const allTime = leaderboardRow(s1);
  assert.equal(allTime.windowReturnWad, allTime.derived.returnWad);
});

test("api reads the state through the getter on every request", async () => {
  // The reducer REPLACES its state object per committed batch; an API bound
  // to a captured state goes stale after batch one (the 2026-10-03 wiring
  // bug: /strategies served the initial empty state while the reducer held
  // the real one). startApi must read through a getter per request.
  let state = makeState();
  const server = startApi(() => state, 0);
  try {
    const base = `http://127.0.0.1:${await ephemeralPort(server)}`;
    const before = (await (await fetch(`${base}/strategies`)).json()) as { rows: RowDto[] };
    assert.equal(before.rows.length, 0);

    // "batch commit": the reducer swaps in a new state object
    state = seedHistory(state, 1, 1);

    const after = (await (await fetch(`${base}/strategies`)).json()) as { rows: RowDto[] };
    assert.equal(after.rows.length, 1);
    assert.equal(after.rows[0]?.id, "1");
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
