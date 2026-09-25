import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { keccak256, toHex, type Address } from "viem";
import {
  applyEvent,
  emptyState,
  isoFromTimestamp,
  type CovenantState,
  type DecodedEvent,
} from "../src/state.js";
import { loadStateFile, saveStateFile, serializeState, deserializeState } from "../src/persist.js";

const CONTRACT = "0x00000000000000000000000000000000000000c0" as Address;
const OPERATOR = "0x0000000000000000000000000000000000000002" as Address;
const CHALLENGER = "0x0000000000000000000000000000000000000004" as Address;

function emit(
  name: string,
  args: Record<string, unknown>,
  blockNumber = 1n,
  blockTimestamp?: bigint,
): DecodedEvent {
  return { eventName: name, args, blockNumber, blockTimestamp };
}

/** A state exercising every epoch status and both timestamp flavors. */
function seededState(): CovenantState {
  let state = emptyState(CONTRACT);
  state = applyEvent(
    state,
    emit("StrategyRegistered", { strategy_id: 1n, owner: OPERATOR, name: "persisted", bond: 1000n }, 5n, 5_000n),
  );
  state = applyEvent(
    state,
    emit(
      "EpochCommitted",
      {
        strategy_id: 1n,
        epoch_index: 0n,
        equity: 1000n,
        net_flow: 1000n,
        trades_root: keccak256(toHex("r0")),
        evidence_uri: "ipfs://e0",
      },
      6n,
      6_000n,
    ),
  );
  state = applyEvent(
    state,
    emit("EpochFinalized", { strategy_id: 1n, epoch_index: 0n, pnl: 50n, equity: 1050n }, 9n, 9_000n),
  );
  state = applyEvent(
    state,
    emit(
      "EpochCommitted",
      {
        strategy_id: 1n,
        epoch_index: 1n,
        equity: 1200n,
        net_flow: 0n,
        trades_root: keccak256(toHex("r1")),
        evidence_uri: "ipfs://e1",
      },
      10n,
      10_000n,
    ),
  );
  state = applyEvent(
    state,
    emit("EpochChallenged", { strategy_id: 1n, epoch_index: 1n, challenger: CHALLENGER, reason: "x" }, 11n, 11_000n),
  );
  state = applyEvent(
    state,
    emit("StrategyRegistered", { strategy_id: 2n, owner: OPERATOR, name: "empty", bond: 5n }, 12n, 12_000n),
  );
  return state;
}

test("serialize -> deserialize is a faithful round-trip", () => {
  const state = seededState();
  const restored = deserializeState(JSON.parse(JSON.stringify(serializeState(state))));
  assert.equal(serializeState(restored).lastBlock, serializeState(state).lastBlock);
  assert.deepEqual(JSON.parse(JSON.stringify(serializeState(restored))), JSON.parse(JSON.stringify(serializeState(state))));
  // derived views are recomputed, not trusted from the file
  const s = restored.strategies.get("1")!;
  assert.equal(s.derived.equity, 1050n);
  assert.equal(s.derived.cumulativePnl, 50n);
  assert.equal(s.derived.returnWad, (50n * 10n ** 18n) / 1050n);
  assert.equal(s.epochs.get("0")!.committedAt, isoFromTimestamp(6_000n));
  assert.equal(s.epochs.get("0")!.finalizedAt, 9_000n);
  assert.equal(s.epochs.get("1")!.status, 2);
});

test("saveStateFile is atomic (no temp leftovers) and loadStateFile resumes it", async () => {
  const dir = await mkdtemp(join(tmpdir(), "covenant-state-"));
  const file = join(dir, "state.json");
  try {
    const state = seededState();
    await saveStateFile(file, state);
    const leftovers = (await readdir(dir)).filter((f) => f.endsWith(".tmp"));
    assert.deepEqual(leftovers, [], "atomic rename leaves no temp files");

    const resumed = await loadStateFile(file);
    assert.ok(resumed !== null);
    assert.equal(resumed.lastBlock, state.lastBlock);
    assert.deepEqual(
      JSON.parse(JSON.stringify(serializeState(resumed))),
      JSON.parse(JSON.stringify(serializeState(state))),
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("loadStateFile returns null for a missing file and throws on corruption", async () => {
  const dir = await mkdtemp(join(tmpdir(), "covenant-state-"));
  try {
    assert.equal(await loadStateFile(join(dir, "absent.json")), null);
    const corrupt = join(dir, "corrupt.json");
    const { writeFile } = await import("node:fs/promises");
    await writeFile(corrupt, "{not json");
    await assert.rejects(loadStateFile(corrupt));
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
