import {
  createPublicClient,
  encodeFunctionData,
  formatUnits,
  http,
  decodeFunctionResult,
  type Address,
  type Chain,
  type PublicClient,
  type Transport,
  type WalletClient,
} from "viem";
import { covenantAbi } from "./abi.js";
import type { CommitPayload } from "./epoch.js";

/**
 * Canonical checkpoint/strategy statuses, mirrored from the contract.
 */
export const CheckpointStatus = {
  Pending: 0,
  Finalized: 1,
  Challenged: 2,
  Invalidated: 3,
} as const;

export const StrategyStatus = { Active: 0, Suspended: 1 } as const;

export interface StrategyInfo {
  id: bigint;
  owner: Address;
  name: string;
  metadataUri: string;
  bond: bigint;
  status: (typeof StrategyStatus)[keyof typeof StrategyStatus];
  createdAt: bigint;
}

export interface PerformanceInfo {
  /** Current equity in USDG base units (6 decimals). */
  equity: bigint;
  highWaterMark: bigint;
  cumulativePnl: bigint;
  epochCount: bigint;
  finalizedEpochs: bigint;
}

export interface CheckpointInfo {
  epochIndex: bigint;
  equity: bigint;
  netFlow: bigint;
  tradesRoot: `0x${string}`;
  evidenceUri: string;
  status: (typeof CheckpointStatus)[keyof typeof CheckpointStatus];
  committedAt: bigint;
  challenger: Address;
  stake: bigint;
}

export interface CovenantConfig {
  admin: Address;
  resolver: Address;
  usdg: Address;
  bondAmount: bigint;
  challengeStake: bigint;
  challengeWindow: bigint;
  paused: boolean;
  treasury: bigint;
}

/** Read-only access to a deployed Covenant contract. */
export class CovenantReader {
  readonly address: Address;
  readonly client: PublicClient;

  constructor(address: Address, client: PublicClient) {
    this.address = address;
    this.client = client;
  }

  static create(address: Address, chain: Chain, rpcUrl: string): CovenantReader {
    return new CovenantReader(address, createPublicClient({ chain, transport: http(rpcUrl) }));
  }

  async config(): Promise<CovenantConfig> {
    const raw = await this.client.readContract({
      address: this.address,
      abi: covenantAbi,
      functionName: "config",
    });
    const [admin, resolver, usdg, bondAmount, challengeStake, challengeWindow, paused, treasury] =
      raw as readonly [Address, Address, Address, bigint, bigint, bigint, boolean, bigint];
    return { admin, resolver, usdg, bondAmount, challengeStake, challengeWindow, paused, treasury };
  }

  async strategyCount(): Promise<bigint> {
    return (await this.client.readContract({
      address: this.address,
      abi: covenantAbi,
      functionName: "strategyCount",
    })) as bigint;
  }

  async getStrategy(strategyId: bigint): Promise<StrategyInfo> {
    const raw = (await this.client.readContract({
      address: this.address,
      abi: covenantAbi,
      functionName: "getStrategy",
      args: [strategyId],
    })) as readonly [Address, string, string, bigint, number, bigint];
    const [owner, name, metadataUri, bond, status, createdAt] = raw;
    return { id: strategyId, owner, name, metadataUri, bond, status: status as 0 | 1, createdAt };
  }

  async getPerformance(strategyId: bigint): Promise<PerformanceInfo> {
    const raw = (await this.client.readContract({
      address: this.address,
      abi: covenantAbi,
      functionName: "getPerformance",
      args: [strategyId],
    })) as readonly [bigint, bigint, bigint, bigint, bigint];
    const [equity, highWaterMark, cumulativePnl, epochCount, finalizedEpochs] = raw;
    return { equity, highWaterMark, cumulativePnl, epochCount, finalizedEpochs };
  }

  async getCheckpoint(strategyId: bigint, epochIndex: bigint): Promise<CheckpointInfo> {
    const raw = (await this.client.readContract({
      address: this.address,
      abi: covenantAbi,
      functionName: "getCheckpoint",
      args: [strategyId, epochIndex],
    })) as readonly [bigint, bigint, `0x${string}`, string, number, bigint, Address, bigint];
    const [equity, netFlow, tradesRoot, evidenceUri, status, committedAt, challenger, stake] = raw;
    return {
      epochIndex,
      equity,
      netFlow,
      tradesRoot,
      evidenceUri,
      status: status as 0 | 1 | 2 | 3,
      committedAt,
      challenger,
      stake,
    };
  }

  /** Verifies a receipt hash onchain (view call). */
  async verifyReceipt(
    strategyId: bigint,
    epochIndex: bigint,
    proof: readonly `0x${string}`[],
    receipt: `0x${string}`,
  ): Promise<boolean> {
    return (await this.client.call({
      to: this.address,
      data: encodeFunctionData({
        abi: covenantAbi,
        functionName: "verifyReceipt",
        args: [strategyId, epochIndex, [...proof], receipt],
      }),
    }).then((r) => {
      if (!r.data) throw new Error("verifyReceipt call returned no data");
      return decodeFunctionResult({
        abi: covenantAbi,
        functionName: "verifyReceipt",
        data: r.data,
      });
    })) as boolean;
  }

  async listStrategies(): Promise<StrategyInfo[]> {
    const count = await this.strategyCount();
    const ids = Array.from({ length: Number(count) }, (_, i) => BigInt(i + 1));
    return Promise.all(ids.map((id) => this.getStrategy(id)));
  }
}

/**
 * Operator client: sends commits and lifecycle transactions from a wallet.
 * The wallet must have approved the USDG bond before registerStrategy.
 */
export class CovenantOperator {
  readonly address: Address;
  readonly wallet: WalletClient;
  readonly reader: CovenantReader;

  constructor(address: Address, wallet: WalletClient, publicClient: PublicClient) {
    this.address = address;
    this.wallet = wallet;
    this.reader = new CovenantReader(address, publicClient);
  }

  async registerStrategy(args: {
    account: Address;
    name: string;
    metadataUri: string;
  }): Promise<{ hash: `0x${string}` }> {
    const hash = await this.wallet.writeContract({
      address: this.address,
      abi: covenantAbi,
      functionName: "registerStrategy",
      args: [args.name, args.metadataUri],
      account: args.account,
      chain: this.wallet.chain,
    });
    if (!hash) throw new Error("wallet returned no transaction hash");
    return { hash };
  }

  /**
   * Commits an epoch built with EpochBuilder. Callers choose confirmation
   * handling; `waitForReceipt` returns the tx receipt when desired.
   */
  async commitEpoch(args: {
    account: Address;
    payload: CommitPayload;
  }): Promise<{ hash: `0x${string}` }> {
    const { payload } = args;
    const hash = await this.wallet.writeContract({
      address: this.address,
      abi: covenantAbi,
      functionName: "commitEpoch",
      args: [
        payload.strategyId,
        payload.epochIndex,
        payload.equityUsdg,
        payload.netFlowUsdg,
        payload.tradesRoot,
        payload.evidenceURI,
      ],
      account: args.account,
      chain: this.wallet.chain,
    });
    if (!hash) throw new Error("wallet returned no transaction hash");
    return { hash };
  }

  async finalizeEpoch(args: {
    account: Address;
    strategyId: bigint;
    epochIndex: bigint;
  }): Promise<{ hash: `0x${string}` }> {
    const hash = await this.wallet.writeContract({
      address: this.address,
      abi: covenantAbi,
      functionName: "finalizeEpoch",
      args: [args.strategyId, args.epochIndex],
      account: args.account,
      chain: this.wallet.chain,
    });
    if (!hash) throw new Error("wallet returned no transaction hash");
    return { hash };
  }

  async challengeEpoch(args: {
    account: Address;
    strategyId: bigint;
    epochIndex: bigint;
    reason: string;
  }): Promise<{ hash: `0x${string}` }> {
    const hash = await this.wallet.writeContract({
      address: this.address,
      abi: covenantAbi,
      functionName: "challengeEpoch",
      args: [args.strategyId, args.epochIndex, args.reason],
      account: args.account,
      chain: this.wallet.chain,
    });
    if (!hash) throw new Error("wallet returned no transaction hash");
    return { hash };
  }
}

/** Formats a USDG base-unit amount (6 decimals) for display. */
export function formatUsdg(baseUnits: bigint): string {
  return formatUnits(baseUnits, 6);
}
