import { createPublicClient, http } from "viem";
import { arbitrum, arbitrumSepolia } from "viem/chains";
import { Indexer } from "./transport.js";
import { createHub, startApi } from "./server.js";
import { DEFAULT_STATE_FILE, loadStateFile, saveStateFile } from "./persist.js";
import type { Address, Chain } from "viem";

/**
 * Entry point. Configuration via environment:
 *   RPC_URL           required
 *   CONTRACT_ADDRESS  required (0x…)
 *   START_BLOCK       optional, defaults to the persisted cursor, then the head
 *   CHAIN             arbitrum | arbitrum-sepolia (default arbitrum-sepolia for staging)
 *   PORT              optional, default 8787
 *   STATE_FILE        optional, default ./covenant-state.json (atomic snapshot,
 *                     written after every batch, resumed on start)
 */

function env(): {
  rpcUrl: string;
  contract: Address;
  chain: Chain;
  startBlock: bigint | null;
  port: number;
  stateFile: string;
} {
  const rpcUrl = process.env.RPC_URL;
  const contract = process.env.CONTRACT_ADDRESS as Address | undefined;
  if (!rpcUrl || !contract) {
    console.error("RPC_URL and CONTRACT_ADDRESS are required");
    process.exit(2);
  }
  const chainName = (process.env.CHAIN ?? "arbitrum-sepolia").toLowerCase();
  const chains: Record<string, Chain> = { arbitrum, "arbitrum-sepolia": arbitrumSepolia };
  const chain = chains[chainName];
  if (!chain) {
    console.error(`unknown CHAIN ${chainName}; use arbitrum or arbitrum-sepolia`);
    process.exit(2);
  }
  return {
    rpcUrl,
    contract,
    chain,
    startBlock: process.env.START_BLOCK ? BigInt(process.env.START_BLOCK) : null,
    port: Number(process.env.PORT ?? 8787),
    stateFile: process.env.STATE_FILE ?? DEFAULT_STATE_FILE,
  };
}

async function main(): Promise<void> {
  const cfg = env();

  // Resume from the snapshot unless START_BLOCK forces a fresh backfill.
  const persisted = cfg.startBlock === null ? await loadStateFile(cfg.stateFile) : null;

  const hub = createHub();
  const indexer = new Indexer({
    contract: cfg.contract,
    chain: cfg.chain,
    rpcUrl: cfg.rpcUrl,
    initialState: persisted ?? undefined,
    onBatch: (state) => {
      hub.emit();
      void saveStateFile(cfg.stateFile, state).catch((e: unknown) =>
        console.error(`state save failed: ${e instanceof Error ? e.message : String(e)}`),
      );
    },
  });

  startApi(indexer.state, cfg.port, hub);
  console.log(`api on :${cfg.port}; indexing ${cfg.contract} on ${cfg.chain.name}`);
  // Explicit START_BLOCK anchors a full backfill; otherwise resume from the
  // snapshot cursor, or follow the head on a cold start.
  const headClient = createPublicClient({ chain: cfg.chain, transport: http(cfg.rpcUrl) });
  const head = await headClient.getBlockNumber();
  const from = cfg.startBlock ?? (persisted !== null ? persisted.lastBlock + 1n : head);
  if (persisted !== null) {
    console.log(`resumed state file ${cfg.stateFile} at block ${persisted.lastBlock}`);
  }

  const sigint = () => {
    indexer.stop();
    process.exit(0);
  };
  process.on("SIGINT", sigint);

  await indexer.run(from);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});