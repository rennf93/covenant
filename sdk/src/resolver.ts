import type { Address, PublicClient, WalletClient } from "viem";
import { covenantAbi } from "./abi.js";
import { wrapContractError } from "./errors.js";
import { sendAndWait, type TxResult } from "./tx.js";

/**
 * Resolver client: rules on challenged checkpoints. Upheld invalidates the
 * epoch and slashes the operator bond to the challenger; dismissed (or forced
 * past the resolver deadline) forfeits the stake and finalizes the checkpoint.
 */
export class CovenantResolver {
  readonly covenant: Address;
  private readonly wallet: WalletClient;
  private readonly client: PublicClient;

  constructor(covenant: Address, wallet: WalletClient, client: PublicClient) {
    this.covenant = covenant;
    this.wallet = wallet;
    this.client = client;
  }

  async resolveChallenge(args: {
    account: Address;
    strategyId: bigint;
    epochIndex: bigint;
    upheld: boolean;
  }): Promise<TxResult> {
    try {
      return await sendAndWait(this.client, () =>
        this.wallet.writeContract({
          address: this.covenant,
          abi: covenantAbi,
          functionName: "resolveChallenge",
          args: [args.strategyId, args.epochIndex, args.upheld],
          account: args.account,
          chain: this.wallet.chain,
        }),
      );
    } catch (e) {
      throw wrapContractError(e);
    }
  }
}
