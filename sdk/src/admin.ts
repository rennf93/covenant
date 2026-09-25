import type { Address, PublicClient, WalletClient } from "viem";
import { covenantAbi } from "./abi.js";
import { wrapContractError } from "./errors.js";
import { writeAndWait, type TxResult } from "./tx.js";

/**
 * Admin client: parameters, pause, treasury, and the two-step admin handover
 * (transferAdmin proposes, acceptAdmin completes; pendingAdmin reads state).
 */
export class CovenantAdmin {
  readonly covenant: Address;
  private readonly wallet: WalletClient;
  private readonly client: PublicClient;

  constructor(covenant: Address, wallet: WalletClient, client: PublicClient) {
    this.covenant = covenant;
    this.wallet = wallet;
    this.client = client;
  }

  private async send(
    call: { functionName: string; args?: readonly unknown[] },
    account: Address,
  ): Promise<TxResult> {
    try {
      return await writeAndWait({
        client: this.client,
        wallet: this.wallet,
        address: this.covenant,
        abi: covenantAbi,
        functionName: call.functionName,
        args: call.args,
        account,
      });
    } catch (e) {
      throw wrapContractError(e);
    }
  }

  async setResolver(args: { account: Address; newResolver: Address }): Promise<TxResult> {
    return this.send({ functionName: "setResolver", args: [args.newResolver] }, args.account);
  }

  async setParameters(args: {
    account: Address;
    bondAmount: bigint;
    challengeStake: bigint;
    challengeWindow: bigint;
  }): Promise<TxResult> {
    return this.send(
      {
        functionName: "setParameters",
        args: [args.bondAmount, args.challengeStake, args.challengeWindow],
      },
      args.account,
    );
  }

  async pause(args: { account: Address }): Promise<TxResult> {
    return this.send({ functionName: "pause" }, args.account);
  }

  async unpause(args: { account: Address }): Promise<TxResult> {
    return this.send({ functionName: "unpause" }, args.account);
  }

  async withdrawTreasury(args: { account: Address; to: Address; amount: bigint }): Promise<TxResult> {
    return this.send(
      { functionName: "withdrawTreasury", args: [args.to, args.amount] },
      args.account,
    );
  }

  /** Handover step 1: propose a successor (zero address clears a proposal). */
  async transferAdmin(args: { account: Address; newAdmin: Address }): Promise<TxResult> {
    return this.send({ functionName: "transferAdmin", args: [args.newAdmin] }, args.account);
  }

  /** Handover step 2: only the proposed successor may accept. */
  async acceptAdmin(args: { account: Address }): Promise<TxResult> {
    return this.send({ functionName: "acceptAdmin" }, args.account);
  }

  /** The currently proposed admin, zero when no handover is pending. */
  async pendingAdmin(): Promise<Address> {
    return (await this.client.readContract({
      address: this.covenant,
      abi: covenantAbi,
      functionName: "pendingAdmin",
    })) as Address;
  }
}
