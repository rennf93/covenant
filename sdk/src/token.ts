import { createPublicClient, http, type Abi, type Address, type Chain, type PublicClient, type WalletClient } from "viem";
import { wrapContractError } from "./errors.js";
import { sendAndWait, type TxResult } from "./tx.js";

/**
 * USDG helpers over a minimal inline ERC-20 ABI: balance, allowance, and
 * approve. Keeping the ABI local means token plumbing never depends on a
 * generated artifact.
 */

export const erc20Abi = [
  { type: "function", name: "name", inputs: [], outputs: [{ type: "string" }], stateMutability: "view" },
  { type: "function", name: "symbol", inputs: [], outputs: [{ type: "string" }], stateMutability: "view" },
  { type: "function", name: "decimals", inputs: [], outputs: [{ type: "uint8" }], stateMutability: "view" },
  {
    type: "function",
    name: "balanceOf",
    inputs: [{ name: "owner", type: "address" }],
    outputs: [{ type: "uint256" }],
    stateMutability: "view",
  },
  {
    type: "function",
    name: "allowance",
    inputs: [
      { name: "owner", type: "address" },
      { name: "spender", type: "address" },
    ],
    outputs: [{ type: "uint256" }],
    stateMutability: "view",
  },
  {
    type: "function",
    name: "approve",
    inputs: [
      { name: "spender", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    outputs: [{ type: "bool" }],
    stateMutability: "nonpayable",
  },
] as const satisfies Abi;

/** Read/write helper for USDG (6 decimals on every chain it deploys to). */
export class UsdgToken {
  readonly address: Address;
  readonly client: PublicClient;
  private readonly wallet?: WalletClient | undefined;

  constructor(address: Address, client: PublicClient, wallet?: WalletClient) {
    this.address = address;
    this.client = client;
    this.wallet = wallet;
  }

  static create(address: Address, chain: Chain, rpcUrl: string): UsdgToken {
    return new UsdgToken(address, createPublicClient({ chain, transport: http(rpcUrl) }));
  }

  async balanceOf(owner: Address): Promise<bigint> {
    return (await this.client.readContract({
      address: this.address,
      abi: erc20Abi,
      functionName: "balanceOf",
      args: [owner],
    })) as bigint;
  }

  async allowance(owner: Address, spender: Address): Promise<bigint> {
    return (await this.client.readContract({
      address: this.address,
      abi: erc20Abi,
      functionName: "allowance",
      args: [owner, spender],
    })) as bigint;
  }

  async decimals(): Promise<number> {
    return (await this.client.readContract({
      address: this.address,
      abi: erc20Abi,
      functionName: "decimals",
    })) as number;
  }

  /** Approves `spender` up to `amount` and waits for the receipt. Needs a wallet. */
  async approve(args: { account: Address; spender: Address; amount: bigint }): Promise<TxResult> {
    const wallet = this.wallet;
    if (!wallet) throw new Error("UsdgToken.approve requires a wallet client");
    try {
      return await sendAndWait(this.client, () =>
        wallet.writeContract({
          address: this.address,
          abi: erc20Abi,
          functionName: "approve",
          args: [args.spender, args.amount],
          account: args.account,
          chain: wallet.chain,
        }),
      );
    } catch (e) {
      throw wrapContractError(e, erc20Abi);
    }
  }
}
