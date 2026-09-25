import type { Address, PublicClient, WalletClient } from "viem";
import { covenantAbi } from "./abi.js";
import { CovenantReader } from "./client.js";
import { wrapContractError } from "./errors.js";
import { UsdgToken } from "./token.js";
import { sendAndWait, type TxResult } from "./tx.js";

/**
 * Challenge-game client: approves the USDG stake when the current allowance
 * does not cover it, files the challenge, and waits for every receipt. The
 * stake defaults to the contract's live challenge_stake parameter.
 */
export class CovenantChallenger {
  readonly covenant: Address;
  readonly usdg: UsdgToken;
  readonly reader: CovenantReader;
  private readonly wallet: WalletClient;
  private readonly client: PublicClient;

  constructor(covenant: Address, usdg: Address, wallet: WalletClient, client: PublicClient) {
    this.covenant = covenant;
    this.wallet = wallet;
    this.client = client;
    this.usdg = new UsdgToken(usdg, client, wallet);
    this.reader = new CovenantReader(covenant, client);
  }

  async challengeEpoch(args: {
    account: Address;
    strategyId: bigint;
    epochIndex: bigint;
    reason: string;
    /** Override for the escrowed stake; defaults to config().challengeStake. */
    stake?: bigint;
  }): Promise<{ approval: TxResult | null; challenge: TxResult }> {
    try {
      const stake = args.stake ?? (await this.reader.config()).challengeStake;
      let approval: TxResult | null = null;
      if ((await this.usdg.allowance(args.account, this.covenant)) < stake) {
        approval = await this.usdg.approve({ account: args.account, spender: this.covenant, amount: stake });
      }
      const challenge = await sendAndWait(this.client, () =>
        this.wallet.writeContract({
          address: this.covenant,
          abi: covenantAbi,
          functionName: "challengeEpoch",
          args: [args.strategyId, args.epochIndex, args.reason],
          account: args.account,
          chain: this.wallet.chain,
        }),
      );
      return { approval, challenge };
    } catch (e) {
      throw wrapContractError(e);
    }
  }
}
