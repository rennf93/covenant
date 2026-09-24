import { keccak256, toHex, type Hash } from "viem";
import { buildMerkleTree, type MerkleTree } from "./merkle.js";
import { receiptHash, validateReceipt, type ReceiptInput } from "./receipt.js";

/**
 * EpochBuilder: accumulates a strategy's fills for one epoch, enforces
 * protocol invariants, and produces the commit payload.
 *
 * Invariants enforced here (mirrored and assumed by the contract):
 *  - a receipt's venueOrderIdHash is unique within the epoch (no double-fills,
 *    no duplicated leaves creating ambiguous trees);
 *  - a receipt's epochIndex matches the builder's epoch;
 *  - receipts are collected with non-decreasing filledAt (the adapter's local
 *    log is append-only; out-of-order fills indicate a sync bug and are refused).
 */
export class EpochBuilder {
  readonly strategyId: bigint;
  readonly epochIndex: bigint;
  private readonly receipts: ReceiptInput[] = [];
  private readonly seenOrderHashes = new Set<string>();
  private lastFilledAt = -1n;

  constructor(strategyId: bigint, epochIndex: bigint) {
    this.strategyId = strategyId;
    this.epochIndex = epochIndex;
  }

  /** Validate and add a fill. Throws on any invariant violation. */
  add(r: ReceiptInput): void {
    if (r.strategyId !== this.strategyId) {
      throw new Error(`receipt strategyId ${r.strategyId} != builder ${this.strategyId}`);
    }
    if (r.epochIndex !== this.epochIndex) {
      throw new Error(`receipt epoch ${r.epochIndex} != builder epoch ${this.epochIndex}`);
    }
    const problems = validateReceipt(r);
    if (problems.length > 0) throw new Error(`invalid receipt: ${problems.join("; ")}`);
    const orderKey = r.venueOrderIdHash.toLowerCase();
    if (this.seenOrderHashes.has(orderKey)) {
      throw new Error(`duplicate venueOrderIdHash in epoch ${this.epochIndex}`);
    }
    if (r.filledAt < this.lastFilledAt) {
      throw new Error(
        `receipt filledAt ${r.filledAt} precedes previously added ${this.lastFilledAt}`,
      );
    }
    this.lastFilledAt = r.filledAt;
    this.seenOrderHashes.add(orderKey);
    this.receipts.push(r);
  }

  get size(): number {
    return this.receipts.length;
  }

  /** Build the Merkle tree. Throws if the epoch has no receipts. */
  tree(): MerkleTree {
    return buildMerkleTree(this.receipts.map(receiptHash));
  }

  /**
   * Commit payload for the contract call.
   * equityUsdg and netFlowUsdg are operator-reported and subject to challenge.
   */
  commitPayload(args: {
    /** Ending equity for the epoch, USDG base units (6 decimals), may be negative. */
    equityUsdg: bigint;
    /**
     * Net external flows this epoch: deposits minus withdrawals, USDG base units.
     * The contract computes PnL = equity_t - equity_{t-1} - netFlow.
     */
    netFlowUsdg: bigint;
    /** Offchain evidence location (e.g. IPFS URI of the full receipt log). */
    evidenceURI: string;
  }): CommitPayload {
    if (this.receipts.length === 0) {
      throw new Error("refusing to commit an epoch with zero receipts");
    }
    const tree = this.tree();
    return {
      strategyId: this.strategyId,
      epochIndex: this.epochIndex,
      equityUsdg: args.equityUsdg,
      netFlowUsdg: args.netFlowUsdg,
      tradesRoot: tree.root,
      evidenceURI: args.evidenceURI,
      receipts: this.receipts.map(receiptHash),
      tree,
    };
  }
}

export interface CommitPayload {
  strategyId: bigint;
  epochIndex: bigint;
  equityUsdg: bigint;
  netFlowUsdg: bigint;
  tradesRoot: Hash;
  evidenceURI: string;
  /** Leaf hashes in insertion order, for publishing alongside the commit. */
  receipts: Hash[];
  tree: MerkleTree;
}

/** Utility: instrument and order-id canonical hashes used in receipts. */
export function canonicalInstrumentHash(instrument: string): Hash {
  return keccak256(toHex(instrument));
}
