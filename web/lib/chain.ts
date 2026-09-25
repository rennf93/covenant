/**
 * Client-side chain plumbing shared by the wallet flow and the verifiers:
 * NEXT_PUBLIC_* env facts, the EIP-1193 injected provider type, viem clients,
 * and one helper that turns any thrown RPC error into an honest one-liner.
 * No wallet SDK: the browser's window.ethereum is reached through viem's
 * custom transport.
 */
import { createPublicClient, http, type Address, type Chain, type PublicClient } from "viem";
import { anvil, arbitrum, arbitrumSepolia } from "viem/chains";
import { covenantAbi, decodeError } from "@covenant/sdk";

/** Minimal EIP-1193 surface the app actually uses. */
export interface Eip1193Provider {
  request(args: { method: string; params?: unknown[] | object }): Promise<unknown>;
  on?(event: string, listener: (...args: never[]) => void): void;
  removeListener?(event: string, listener: (...args: never[]) => void): void;
}

declare global {
  interface Window {
    ethereum?: Eip1193Provider | undefined;
  }
}

export const CHAINS: Record<string, Chain> = {
  arbitrum,
  "arbitrum-sepolia": arbitrumSepolia,
  anvil,
};

/** Configured chain name; matches the indexer's chain vocabulary. */
export function chainName(): string {
  return process.env.NEXT_PUBLIC_CHAIN ?? "arbitrum-sepolia";
}

export function configuredChain(): Chain | null {
  return CHAINS[chainName()] ?? null;
}

/** Deployed Covenant contract, or null in demo mode (nothing deployed). */
export function contractAddress(): Address | null {
  const raw = process.env.NEXT_PUBLIC_CONTRACT_ADDRESS ?? "";
  return /^0x[0-9a-fA-F]{40}$/.test(raw) ? (raw as Address) : null;
}

export function rpcUrl(): string {
  return process.env.NEXT_PUBLIC_RPC_URL ?? "";
}

/** True only when every fact a browser-side chain read or write needs exists. */
export function chainConfigured(): boolean {
  return contractAddress() !== null && configuredChain() !== null && rpcUrl() !== "";
}

/** Read-only client against the configured RPC; null when unconfigured. */
export function publicClient(): PublicClient | null {
  const chain = configuredChain();
  const rpc = rpcUrl();
  if (chain === null || rpc === "") return null;
  return createPublicClient({ chain, transport: http(rpc) });
}

/** Strategy owner straight from the contract's getStrategy view; null when the chain is unreachable or unconfigured. */
export async function readStrategyOwner(strategyId: bigint): Promise<Address | null> {
  const contract = contractAddress();
  const client = publicClient();
  if (contract === null || client === null) return null;
  try {
    const raw = await client.readContract({
      address: contract,
      abi: covenantAbi,
      functionName: "getStrategy",
      args: [strategyId],
    });
    const owner = (raw as readonly [Address, ...unknown[]])[0];
    return owner ?? null;
  } catch {
    return null;
  }
}

/**
 * One honest line for any thrown RPC/wallet error: the decoded Covenant
 * revert name when the selector matches the ABI, else viem's short message.
 */
export function txErrorMessage(err: unknown): string {
  try {
    const decoded = decodeError(err);
    if (decoded.matched) return `contract reverted: ${decoded.name}`;
  } catch {
    // fall through to the raw message
  }
  if (err instanceof Error) {
    const short = (err as { shortMessage?: string }).shortMessage;
    return short ?? err.message;
  }
  return String(err);
}
