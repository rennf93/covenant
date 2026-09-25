import assert from "node:assert/strict";
import { test } from "node:test";
import { encodeErrorResult, encodeFunctionData, type Abi, type Address, type PublicClient, type TransactionReceipt, type WalletClient } from "viem";
import {
  CheckpointStatus,
  CovenantAdmin,
  CovenantChallenger,
  CovenantError,
  CovenantOperator,
  CovenantResolver,
  UsdgToken,
  covenantAbi,
  decodeError,
  erc20Abi,
  errorSelector,
  findDueCheckpoints,
  revertData,
  wrapContractError,
  type CheckpointTiming,
} from "../src/index.js";

const COVENANT = "0x00000000000000000000000000000000000000c1" as Address;
const USDG = "0x00000000000000000000000000000000000000d1" as Address;
const ACCOUNT = "0x00000000000000000000000000000000000000a1" as Address;

interface Call {
  to: string;
  functionName: string;
  args: unknown;
}

interface Harness {
  writes: Call[];
  reads: Call[];
  waits: string[];
  publicClient: PublicClient;
  wallet: WalletClient;
}

/**
 * Fake public client + wallet in the existing test style: reads are answered
 * from a queue, writes are recorded and confirmed from a receipt queue.
 */
function makeHarness(opts: {
  readResults: unknown[];
  receipts?: number[];
}): Harness {
  const writes: Call[] = [];
  const reads: Call[] = [];
  const waits: string[] = [];
  const readResults = [...opts.readResults];
  const receipts = [...(opts.receipts ?? [])];
  const receipt = (n: number) => ({ status: "success", blockNumber: BigInt(n), gasUsed: 50_000n }) as unknown as TransactionReceipt;
  let hashCount = 0;

  const publicClient = {
    readContract: async (args: { functionName: string; args?: unknown }) => {
      reads.push({ to: "", functionName: args.functionName, args: args.args });
      const next = readResults.shift();
      if (next === undefined) throw new Error("no scripted read result for " + String(args.functionName));
      return typeof next === "function" ? (next as () => unknown)() : next;
    },
    waitForTransactionReceipt: async ({ hash }: { hash: string }) => {
      waits.push(hash);
      return receipt(receipts.length > 0 ? receipts.shift()! : 1);
    },
    call: async () => {
      throw new Error("unexpected call()");
    },
  } as unknown as PublicClient;

  const wallet = {
    chain: { id: 421614 },
    writeContract: async (args: { address: Address; functionName: string; args?: unknown }) => {
      writes.push({ to: args.address, functionName: args.functionName, args: args.args });
      return `0xhash${++hashCount}` as `0x${string}`;
    },
  } as unknown as WalletClient;

  return { writes, reads, waits, publicClient, wallet };
}

test("decodeError maps custom selectors from the abi array generically", () => {
  // an error the contract could add tomorrow decodes with no code change here
  const abi = [
    ...covenantAbi,
    {
      type: "error",
      name: "StakeEscrowFailed",
      inputs: [
        { name: "strategy_id", type: "uint256" },
        { name: "epoch_index", type: "uint64" },
      ],
    },
  ] as const satisfies Abi;

  const data = encodeErrorResult({
    abi,
    errorName: "StakeEscrowFailed",
    args: [7n, 3n],
  });
  const err = { cause: { data } };
  const decoded = decodeError(err, abi);
  assert.equal(decoded.matched, true);
  assert.equal(decoded.name, "StakeEscrowFailed");
  assert.deepEqual(decoded.args, { strategy_id: 7n, epoch_index: 3n });
  assert.equal(decoded.selector, data.slice(0, 10));

  // the contract's own error set decodes straight from covenantAbi
  const plain = decodeError({ cause: { data: encodeErrorResult({ abi, errorName: "NotResolver", args: [] }) } }, abi);
  assert.equal(plain.name, "NotResolver");
  assert.deepEqual(plain.args, {});

  // unknown selector: named honestly, never fabricated
  const stranger = decodeError({ cause: { data: "0xdeadbeef" + "11".repeat(31) } }, abi);
  assert.equal(stranger.matched, false);
  assert.equal(stranger.name, "UnknownError");
  assert.equal(stranger.selector, "0xdeadbeef");
});

test("decodeError walks viem-shaped chains and tolerates missing data", () => {
  assert.equal(revertData(new Error("plain")), null);
  assert.equal(
    revertData({ error: { cause: { data: { data: "0x12345678" + "00".repeat(31) } } } }),
    "0x12345678" + "00".repeat(31),
  );
  const empty = decodeError(new Error("network down"));
  assert.equal(empty.matched, false);
  assert.equal(empty.selector, null);
  assert.equal(empty.data, null);
});

test("wrapContractError decorates matched reverts and passes the rest through", () => {
  const abi = covenantAbi;
  const original = Object.assign(new Error("boom"), {
    cause: { data: encodeErrorResult({ abi, errorName: "ContractPaused", args: [] }) },
  });
  const wrapped = wrapContractError(original, abi);
  assert.ok(wrapped instanceof CovenantError);
  assert.match((wrapped as CovenantError).message, /contract reverted: ContractPaused/);
  assert.equal((wrapped as CovenantError).decoded.name, "ContractPaused");
  const passthrough = wrapContractError(new Error("network"), abi);
  assert.ok(passthrough instanceof Error && !(passthrough instanceof CovenantError));
  assert.match(passthrough.message, /network/);
});

test("errorSelector matches the first four bytes of keccak of the signature", () => {
  const item = covenantAbi.find((i) => i.type === "error" && i.name === "ZeroAmount");
  assert.ok(item !== undefined, "covenantAbi carries the contract's error set");
  const selector = errorSelector(item);
  const data = encodeErrorResult({ abi: covenantAbi, errorName: "ZeroAmount", args: [] });
  assert.equal(selector, data.slice(0, 10));
  assert.equal(errorSelector({ type: "function", name: "x" }), null);
});

test("operator methods wait for receipts and keep their public names", async () => {
  const h = makeHarness({ readResults: [], receipts: [11, 12] });
  const operator = new CovenantOperator(COVENANT, h.wallet, h.publicClient);

  const registered = await operator.registerStrategy({ account: ACCOUNT, name: "n", metadataUri: "m" });
  assert.match(registered.hash, /^0xhash1$/);
  assert.equal(registered.receipt.blockNumber, 11n);
  assert.deepEqual(h.writes, [{ to: COVENANT, functionName: "registerStrategy", args: ["n", "m"] }]);
  assert.deepEqual(h.waits, [registered.hash]);

  const finalized = await operator.finalizeEpoch({ account: ACCOUNT, strategyId: 1n, epochIndex: 0n });
  assert.deepEqual(h.writes[1], { to: COVENANT, functionName: "finalizeEpoch", args: [1n, 0n] });
  assert.equal(finalized.receipt.blockNumber, 12n);
});

test("UsdgToken reads balance/allowance and approves with a wallet", async () => {
  const h = makeHarness({ readResults: [1_234n, 5n, 6], receipts: [7] });
  const token = new UsdgToken(USDG, h.publicClient, h.wallet);
  assert.equal(await token.balanceOf(ACCOUNT), 1_234n);
  assert.deepEqual(h.reads[0], { to: "", functionName: "balanceOf", args: [ACCOUNT] });
  assert.equal(await token.allowance(ACCOUNT, COVENANT), 5n);
  assert.equal(await token.decimals(), 6);

  const approval = await token.approve({ account: ACCOUNT, spender: COVENANT, amount: 5n });
  assert.deepEqual(h.writes, [{ to: USDG, functionName: "approve", args: [COVENANT, 5n] }]);
  assert.equal(approval.receipt.blockNumber, 7n);

  const readOnly = new UsdgToken(USDG, h.publicClient);
  await assert.rejects(readOnly.approve({ account: ACCOUNT, spender: COVENANT, amount: 1n }), /requires a wallet/);
});

test("CovenantChallenger approves only the missing allowance, then challenges", async () => {
  // allowance 2 < stake 5 -> approve(5) then challenge
  const low = makeHarness({ readResults: [[ACCOUNT, ACCOUNT, USDG, 10n, 5n, 300n, false, 0n], 2n], receipts: [1, 2] });
  const challenger = new CovenantChallenger(COVENANT, USDG, low.wallet, low.publicClient);
  const { approval, challenge } = await challenger.challengeEpoch({
    account: ACCOUNT,
    strategyId: 1n,
    epochIndex: 0n,
    reason: "equity lie",
  });
  assert.ok(approval !== null && approval.receipt.blockNumber === 1n);
  assert.equal(challenge.receipt.blockNumber, 2n);
  assert.deepEqual(low.writes[0], { to: USDG, functionName: "approve", args: [COVENANT, 5n] });
  assert.deepEqual(low.writes[1], { to: COVENANT, functionName: "challengeEpoch", args: [1n, 0n, "equity lie"] });

  // sufficient allowance skips the approval entirely
  const funded = makeHarness({ readResults: [[ACCOUNT, ACCOUNT, USDG, 10n, 5n, 300n, false, 0n], 9n], receipts: [3] });
  const quiet = new CovenantChallenger(COVENANT, USDG, funded.wallet, funded.publicClient);
  const skip = await quiet.challengeEpoch({ account: ACCOUNT, strategyId: 1n, epochIndex: 1n, reason: "again" });
  assert.equal(skip.approval, null);
  assert.deepEqual(funded.writes, [{ to: COVENANT, functionName: "challengeEpoch", args: [1n, 1n, "again"] }]);

  // explicit stake overrides config
  const explicit = makeHarness({ readResults: [8n], receipts: [4] });
  const custom = new CovenantChallenger(COVENANT, USDG, explicit.wallet, explicit.publicClient);
  await custom.challengeEpoch({ account: ACCOUNT, strategyId: 1n, epochIndex: 2n, reason: "r", stake: 8n });
  assert.deepEqual(explicit.reads, [{ to: "", functionName: "allowance", args: [ACCOUNT, COVENANT] }]);
});

test("CovenantResolver resolves with a waited receipt", async () => {
  const h = makeHarness({ readResults: [], receipts: [5] });
  const resolver = new CovenantResolver(COVENANT, h.wallet, h.publicClient);
  const res = await resolver.resolveChallenge({ account: ACCOUNT, strategyId: 1n, epochIndex: 0n, upheld: true });
  assert.deepEqual(h.writes, [{ to: COVENANT, functionName: "resolveChallenge", args: [1n, 0n, true] }]);
  assert.equal(res.receipt.blockNumber, 5n);
});

test("CovenantAdmin covers parameters, pause, treasury, and the two-step handover", async () => {
  const h = makeHarness({ readResults: [ACCOUNT], receipts: [1, 2, 3, 4, 5, 6, 7] });
  const admin = new CovenantAdmin(COVENANT, h.wallet, h.publicClient);

  await admin.setResolver({ account: ACCOUNT, newResolver: ACCOUNT });
  await admin.setParameters({ account: ACCOUNT, bondAmount: 10n, challengeStake: 5n, challengeWindow: 300n });
  await admin.pause({ account: ACCOUNT });
  await admin.unpause({ account: ACCOUNT });
  await admin.withdrawTreasury({ account: ACCOUNT, to: ACCOUNT, amount: 1n });
  await admin.transferAdmin({ account: ACCOUNT, newAdmin: ACCOUNT });
  await admin.acceptAdmin({ account: ACCOUNT });

  assert.deepEqual(
    h.writes.map((w) => w.functionName),
    ["setResolver", "setParameters", "pause", "unpause", "withdrawTreasury", "transferAdmin", "acceptAdmin"],
  );
  assert.deepEqual(h.writes[1]!.args, [10n, 5n, 300n]);
  assert.deepEqual(h.writes[4]!.args, [ACCOUNT, 1n]);
  assert.equal(await admin.pendingAdmin(), ACCOUNT);
  assert.deepEqual(h.reads[0], { to: "", functionName: "pendingAdmin", args: undefined });
});

test("findDueCheckpoints returns exactly the window-elapsed pending checkpoints", () => {
  const now = 1_000_000n;
  const window = 300n;
  const checkpoints: CheckpointTiming[] = [
    { strategyId: 2n, epochIndex: 0n, status: CheckpointStatus.Pending, committedAt: now - window }, // due on the dot
    { strategyId: 2n, epochIndex: 1n, status: CheckpointStatus.Pending, committedAt: now - window + 1n }, // one second short
    { strategyId: 1n, epochIndex: 3n, status: CheckpointStatus.Pending, committedAt: 0n }, // ancient -> first
    { strategyId: 1n, epochIndex: 4n, status: CheckpointStatus.Finalized, committedAt: 0n }, // already done
    { strategyId: 1n, epochIndex: 5n, status: CheckpointStatus.Challenged, committedAt: 0n }, // frozen
    { strategyId: 1n, epochIndex: 6n, status: CheckpointStatus.Invalidated, committedAt: 0n }, // dead
  ];
  const due = findDueCheckpoints({ nowSec: now, windowSec: window, checkpoints });
  assert.deepEqual(
    due.map((d) => [d.strategyId, d.epochIndex]),
    [
      [1n, 3n],
      [2n, 0n],
    ],
  );
  assert.equal(due[1]!.finalizableAt, now);
});

test("encodeFunctionData round-trips the two-step handover names from covenantAbi", () => {
  const data = encodeFunctionData({ abi: covenantAbi, functionName: "transferAdmin", args: [ACCOUNT] });
  assert.match(data, /^0x[0-9a-f]{8}/);
  const approve = erc20Abi.find((item) => "name" in item && item.name === "approve");
  assert.ok(approve !== undefined, "erc20Abi exposes approve");
});
