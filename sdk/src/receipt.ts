import { encodeAbiParameters, keccak256, type Hash } from "viem";

/**
 * Canonical trade receipt, hashable and Merkle-attestable onchain.
 *
 * This encoding is the single source of truth shared by:
 *  - the Stylus contract (verifyReceipt against a committed epoch root)
 *  - this SDK (operators building epoch trees)
 *  - the indexer and web verifier UI
 *
 * Any change here is a protocol change and MUST be mirrored in
 * contracts/src/lib.rs and validated by cross-language fixture tests.
 */

/** Trading venue class of the fill. */
export enum Venue {
  Coinbase = 0,
  ArbitrumDex = 1,
  Other = 2,
}

/** Fill side from the strategy's perspective. */
export enum Side {
  Buy = 0,
  Sell = 1,
}

export interface ReceiptInput {
  /** Covenant strategy id (assigned by the registry). */
  strategyId: bigint;
  /** Epoch this fill belongs to. */
  epochIndex: bigint;
  /** Venue class. */
  venue: Venue;
  /**
   * keccak256 of the venue's order/fill id. We commit the hash, not the raw id,
   * so operators never publish CEX account identifiers, yet a disclosed fill
   * can still be matched to a venue statement line by the operator.
   */
  venueOrderIdHash: Hash;
  /**
   * keccak256 of the canonical instrument string, e.g.
   * keccak256(toHex("SOL-USD")) on Coinbase, keccak256(toHex("SOL/USDC"))
   * for the Arbitrum DEX venue. Canonicalization rules per venue live in the adapter.
   */
  instrumentHash: Hash;
  /** Buy or sell. */
  side: Side;
  /**
   * Signed base-asset size in the instrument's base units (positive = acquired,
   * negative = disposed). Sign convention: buys are positive size with side=Buy;
   * sells carry negative size with side=Sell. Redundant but defensive: a receipt
   * whose side and size sign disagree must be rejected by adapters before commit.
   */
  sizeSignedBase: bigint;
  /** Fill price in quote units, 8 decimals fixed point. */
  priceQuote: bigint;
  /** Fee paid, in quote units, 8 decimals fixed point. */
  feeQuote: bigint;
  /** Fill time, unix seconds. */
  filledAt: bigint;
}

const receiptParameters = [
  { type: "uint256", name: "strategyId" },
  { type: "uint64", name: "epochIndex" },
  { type: "uint8", name: "venue" },
  { type: "bytes32", name: "venueOrderIdHash" },
  { type: "bytes32", name: "instrumentHash" },
  { type: "uint8", name: "side" },
  { type: "int256", name: "sizeSignedBase" },
  { type: "uint256", name: "priceQuote" },
  { type: "uint256", name: "feeQuote" },
  { type: "uint64", name: "filledAt" },
] as const;

/** Validate invariants that the ABI encoding cannot express. */
export function validateReceipt(r: ReceiptInput): string[] {
  const errors: string[] = [];
  if (r.strategyId < 0n) errors.push("strategyId must be non-negative");
  if (r.epochIndex < 0n) errors.push("epochIndex must be non-negative");
  if (r.filledAt < 0n || r.filledAt > 2n ** 64n - 1n)
    errors.push("filledAt must fit uint64");
  if (r.priceQuote > 2n ** 256n - 1n) errors.push("priceQuote must fit uint256");
  if (r.feeQuote > 2n ** 256n - 1n) errors.push("feeQuote must fit uint256");
  if (r.sizeSignedBase >= 2n ** 255n || r.sizeSignedBase < -(2n ** 255n))
    errors.push("sizeSignedBase must fit int256");
  if (r.side === Side.Buy && r.sizeSignedBase <= 0n)
    errors.push("Buy receipt must carry positive size");
  if (r.side === Side.Sell && r.sizeSignedBase >= 0n)
    errors.push("Sell receipt must carry negative size");
  if (r.priceQuote === 0n) errors.push("priceQuote must be positive");
  return errors;
}

/** ABI-encode the receipt exactly as the contract expects. */
export function encodeReceipt(r: ReceiptInput): Hash {
  return encodeAbiParameters(receiptParameters, [
    r.strategyId,
    r.epochIndex,
    r.venue,
    r.venueOrderIdHash,
    r.instrumentHash,
    r.side,
    r.sizeSignedBase,
    r.priceQuote,
    r.feeQuote,
    r.filledAt,
  ]);
}

/** Canonical leaf hash for a receipt. Rejects structurally invalid receipts. */
export function receiptHash(r: ReceiptInput): Hash {
  const problems = validateReceipt(r);
  if (problems.length > 0) {
    throw new Error(`invalid receipt: ${problems.join("; ")}`);
  }
  return keccak256(encodeReceipt(r));
}

/**
 * Build a receipt from human units. Convenience for adapters; adapters remain
 * responsible for venue-specific canonicalization (instrument strings, ids).
 */
export function makeReceipt(args: {
  strategyId: bigint;
  epochIndex: bigint;
  venue: Venue;
  venueOrderId: string;
  instrument: string;
  side: Side;
  /** Human-readable base size, e.g. 1.25 SOL. Accepts decimal strings for exactness. */
  sizeBase: number | string;
  /** Human price in quote, e.g. 147.32 USD. Accepts decimal strings for exactness. */
  price: number | string;
  /** Human fee in quote. Accepts decimal strings for exactness. */
  fee: number | string;
  /** Fill time, unix seconds. */
  filledAt: number;
}): ReceiptInput {
  const hashOf = (s: string) =>
    keccak256(Buffer.from(s, "utf8")) as Hash;
  const side = args.side;
  const signed = side === Side.Buy ? args.sizeBase : -args.sizeBase;
  return {
    strategyId: args.strategyId,
    epochIndex: args.epochIndex,
    venue: args.venue,
    venueOrderIdHash: hashOf(args.venueOrderId),
    instrumentHash: hashOf(args.instrument),
    side,
    sizeSignedBase: parseFixed8(signed),
    priceQuote: parseFixed8(args.price),
    feeQuote: parseFixed8(args.fee),
    filledAt: BigInt(args.filledAt),
  };
}

/** Parse a decimal string/number into 8-decimals fixed point bigint, exactly. */
export function parseFixed8(x: number | string): bigint {
  const s = typeof x === "number" ? x.toString() : x;
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s);
  if (!m) throw new Error(`invalid decimal: ${s}`);
  if ((m[3] ?? "").length > 8) {
    throw new Error(`more than 8 fractional digits would lose precision: ${s}`);
  }
  const sign = m[1] === "-" ? -1n : 1n;
  const whole = m[2] ?? "0";
  const frac = (m[3] ?? "").padEnd(8, "0");
  return sign * (BigInt(whole) * 100_000_000n + BigInt(frac || "0"));
}
