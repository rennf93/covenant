import { createPublicClient, http } from "viem";
import { arbitrum, arbitrumSepolia } from "viem/chains";
import { Indexer } from "./transport.js";
import { startApi } from "./server.js";
import type { Address, Chain } from "viem";

/**
 * Entry point. Configuration via environment:
 *   RPC_URL           required
 *   CONTRACT_ADDRESS  required (0x…)
 *   START_BLOCK       optional, defaults to following the head
 *   CHAIN             arbitrum | arbitrum-sepolia (default arbitrum-sepololia for staging)
 *   PORT              optional, default 8787
 */

function env(): { rpcUrl: string; contract: Address; chain: Chain; startBlock: bigint | null; port: number } {
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
  };
}

async function main(): Promise<void> {
  const cfg = env();
  const indexer = new Indexer({
    contract: cfg.contract,
    chain: cfg.chain,
    rpcUrl: cfg.rpcUrl,
  });

  startApi(indexer.state, cfg.port);
  console.log(`api on :${cfg.port}; indexing ${cfg.contract} on ${cfg.chain.name}`);

  // Follow the head by default; explicit START_BLOCK anchors a full backfill.
  const headClient = createPublicClient({ chain: cfg.chain, transport: http(cfg.rpcUrl) });
  const head = await headClient.getBlockNumber();
  const from = cfg.startBlock ?? head;

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
