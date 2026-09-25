/**
 * Finalizer: the piece that makes onchain PnL actually accrue. Polls every
 * strategy via CovenantReader, finds Pending checkpoints whose challenge
 * window (config().challengeWindow) has fully elapsed, and finalizes them.
 *
 * DRY-RUN by default: prints the plan and exits. Pass --execute to actually
 * send the finalizeEpoch transactions from the funded key. A per-tx failure is
 * decoded (CovenantError) and reported; remaining txs still run.
 *
 * Env: RPC_URL, CONTRACT_ADDRESS, PRIVATE_KEY (required with --execute),
 *      CHAIN (arbitrum | arbitrum-sepolia, default arbitrum-sepolia).
 * Run: pnpm --filter @covenant/sdk run finalize
 *
 * Imports the built SDK from dist so plain node runs it (no ts runtime dep).
 */

import { createPublicClient, createWalletClient, http, type Address, type Chain } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { arbitrum, arbitrumSepolia } from "viem/chains";
import {
  CheckpointStatus,
  CovenantOperator,
  CovenantReader,
  decodeError,
  findDueCheckpoints,
  type CheckpointTiming,
} from "../dist/src/index.js";

const CHAINS: Record<string, Chain> = { arbitrum, "arbitrum-sepolia": arbitrumSepolia };

function envOrDie(): { rpcUrl: string; contract: Address; chain: Chain } {
  const rpcUrl = process.env.RPC_URL;
  const contract = process.env.CONTRACT_ADDRESS as Address | undefined;
  if (!rpcUrl || !contract) {
    console.error("RPC_URL and CONTRACT_ADDRESS are required");
    process.exit(2);
  }
  const chain = CHAINS[(process.env.CHAIN ?? "arbitrum-sepolia").toLowerCase()];
  if (!chain) {
    console.error("unknown CHAIN; use arbitrum or arbitrum-sepolia");
    process.exit(2);
  }
  return { rpcUrl, contract, chain };
}

function iso(sec: bigint): string {
  return new Date(Number(sec) * 1000).toISOString();
}

/** Fixed-width table printer (dependency-free, like everything here). */
function printTable(header: string[], rows: string[][]): void {
  const widths = header.map((h, i) => Math.max(h.length, ...rows.map((r) => r[i]!.length)));
  const line = (cells: string[]) =>
    cells.map((c, i) => (i === cells.length - 1 ? c : c.padEnd(widths[i]!))).join("  ");
  console.log(line(header));
  console.log(widths.map((w) => "-".repeat(w)).join("  "));
  for (const row of rows) console.log(line(row));
}

async function main(): Promise<void> {
  const cfg = envOrDie();
  const execute = process.argv.includes("--execute");
  const client = createPublicClient({ chain: cfg.chain, transport: http(cfg.rpcUrl) });
  const reader = new CovenantReader(cfg.contract, client);

  const config = await reader.config();
  const nowSec = BigInt(Math.floor(Date.now() / 1000));
  console.log(
    `covenant ${cfg.contract} on ${cfg.chain.name}: window ${config.challengeWindow}s, ` +
      `now ${iso(nowSec)}, mode ${execute ? "EXECUTE" : "DRY-RUN"}`,
  );

  // Discovery: every strategy, every committed checkpoint, exactly once.
  const count = await reader.strategyCount();
  const timings: CheckpointTiming[] = [];
  for (let id = 1n; id <= count; id++) {
    const perf = await reader.getPerformance(id);
    for (let epoch = 0n; epoch < perf.epochCount; epoch++) {
      const cp = await reader.getCheckpoint(id, epoch);
      if (cp.status !== CheckpointStatus.Pending) continue;
      timings.push({ strategyId: id, epochIndex: epoch, status: cp.status, committedAt: cp.committedAt });
    }
  }
  const due = findDueCheckpoints({ nowSec, windowSec: config.challengeWindow, checkpoints: timings });

  printTable(
    ["strategy", "epoch", "committed", "window closed", "action"],
    due.length > 0
      ? due.map((d) => [
          d.strategyId.toString(),
          d.epochIndex.toString(),
          iso(d.committedAt),
          iso(d.finalizableAt),
          execute ? "finalize" : "would finalize",
        ])
      : [["-", "-", "-", "-", "nothing due"]],
  );

  if (due.length === 0) {
    console.log("no checkpoints are due; nothing to do");
    return;
  }
  if (!execute) {
    console.log(`DRY-RUN: ${due.length} checkpoint(s) would be finalized; pass --execute to send`);
    return;
  }

  const key = process.env.PRIVATE_KEY;
  if (!key) {
    console.error("PRIVATE_KEY is required with --execute");
    process.exit(2);
  }
  const account = privateKeyToAccount(key as `0x${string}`);
  const wallet = createWalletClient({ account, chain: cfg.chain, transport: http(cfg.rpcUrl) });
  const operator = new CovenantOperator(cfg.contract, wallet, client);
  console.log(`executing as ${account.address}`);

  let failures = 0;
  for (const d of due) {
    try {
      const result = await operator.finalizeEpoch({
        account: account.address,
        strategyId: d.strategyId,
        epochIndex: d.epochIndex,
      });
      console.log(
        `finalized strategy ${d.strategyId} epoch ${d.epochIndex}: ${result.hash} (block ${result.receipt.blockNumber})`,
      );
    } catch (e) {
      failures += 1;
      const decoded = decodeError(e);
      console.error(
        `FAILED strategy ${d.strategyId} epoch ${d.epochIndex}: ` +
          `${e instanceof Error ? e.message : String(e)}` +
          (decoded.matched ? ` [${decoded.name}]` : ""),
      );
    }
  }
  if (failures > 0) {
    console.error(`${failures} finalize(s) failed`);
    process.exit(1);
  }
  console.log(`done: ${due.length} checkpoint(s) finalized`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
