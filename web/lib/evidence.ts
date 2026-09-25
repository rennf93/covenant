/**
 * Signed evidence bundle verification, browser-side and dependency-free of
 * this website's backend: the schema is pinned by the producer (vouch attest,
 * bundle v2), the leaf encoding is @covenant/sdk's receipt encoding, and the
 * signature is an EIP-712 typed-data signature over the epoch commitment.
 *
 * Bundle v2 fields: signer, chainId, contract, signature (0x, 65-byte rsv),
 * signedAt, plus the original v1 fields with equityUsdg/netFlowUsdg as
 * base-unit integer strings and receipts as before. v1 bundles (unsigned,
 * numeric equity fields) parse too and are reported as unsigned.
 */
import { receiptHash, verifyProof, Side, Venue, type ReceiptInput } from "@covenant/sdk";
import { hashTypedData, recoverAddress, verifyTypedData, type Address, type Hash } from "viem";

export interface EvidenceReceiptEntry {
  receipt: {
    strategyId: string;
    epochIndex: string;
    venue: number;
    venueOrderIdHash: string;
    instrumentHash: string;
    side: number;
    sizeSignedBase: string;
    priceQuote: string;
    feeQuote: string;
    filledAt: string;
  };
  hash: string;
  proof: string[];
}

export interface EvidenceBundle {
  protocol: string;
  strategyId: string;
  strategyName: string;
  epochIndex: string;
  /** Unix seconds, decimal string. */
  committedAt: string;
  /** USDG base units, integer string (may be negative). */
  equityUsdg: string;
  netFlowUsdg: string;
  tradesRoot: string;
  venue: number;
  instrument: string;
  receipts: EvidenceReceiptEntry[];
  /** v2 only. */
  signer: string | null;
  chainId: number | null;
  contract: string | null;
  signature: string | null;
  signedAt: string | null;
}

export interface ReceiptCheck {
  index: number;
  hash: string;
  /** Recomputing the canonical leaf from the receipt fields reproduces hash. */
  hashOk: boolean;
  /** Walking proof from the declared hash lands on the bundle tradesRoot. */
  proofOk: boolean;
  ok: boolean;
}

export interface SignatureCheck {
  /** False for v1 bundles that carry no signature at all. */
  present: boolean;
  ok: boolean;
  recovered: string | null;
  expected: string | null;
  expectedSource: "chain-owner" | "bundle-signer" | "none";
  detail: string;
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function intString(v: unknown, field: string): string {
  if (typeof v === "number" && Number.isInteger(v)) return v.toString();
  if (typeof v === "bigint") return v.toString();
  if (typeof v === "string" && /^-?\d+$/.test(v)) return v;
  throw new Error(`field ${field} must be an integer (got ${JSON.stringify(v) ?? "?"})`);
}

function hex32(v: unknown, field: string): string {
  if (typeof v === "string" && /^0x[0-9a-fA-F]{64}$/.test(v)) return v;
  throw new Error(`field ${field} must be a 32-byte hex hash`);
}

function addr(v: unknown, field: string): string {
  if (typeof v === "string" && /^0x[0-9a-fA-F]{40}$/.test(v)) return v;
  throw new Error(`field ${field} must be a 20-byte hex address`);
}

/** Parses and validates a bundle; throws with the first structural problem found. */
export function parseEvidenceBundle(raw: unknown): EvidenceBundle {
  if (!isObj(raw)) throw new Error("evidence bundle is not a JSON object");
  const receiptsRaw = raw.receipts;
  if (!Array.isArray(receiptsRaw)) throw new Error("field receipts must be an array");
  const receipts: EvidenceReceiptEntry[] = receiptsRaw.map((entry, i) => {
    if (!isObj(entry) || !isObj(entry.receipt)) {
      throw new Error(`receipts[${i}] must carry a receipt object`);
    }
    const r = entry.receipt;
    const proofRaw = entry.proof;
    if (!Array.isArray(proofRaw)) throw new Error(`receipts[${i}].proof must be an array`);
    return {
      receipt: {
        strategyId: intString(r.strategyId, `receipts[${i}].receipt.strategyId`),
        epochIndex: intString(r.epochIndex, `receipts[${i}].receipt.epochIndex`),
        venue: Number(intString(r.venue, `receipts[${i}].receipt.venue`)),
        venueOrderIdHash: hex32(r.venueOrderIdHash, `receipts[${i}].receipt.venueOrderIdHash`),
        instrumentHash: hex32(r.instrumentHash, `receipts[${i}].receipt.instrumentHash`),
        side: Number(intString(r.side, `receipts[${i}].receipt.side`)),
        sizeSignedBase: intString(r.sizeSignedBase, `receipts[${i}].receipt.sizeSignedBase`),
        priceQuote: intString(r.priceQuote, `receipts[${i}].receipt.priceQuote`),
        feeQuote: intString(r.feeQuote, `receipts[${i}].receipt.feeQuote`),
        filledAt: intString(r.filledAt, `receipts[${i}].receipt.filledAt`),
      },
      hash: hex32(entry.hash, `receipts[${i}].hash`),
      proof: proofRaw.map((p, j) => hex32(p, `receipts[${i}].proof[${j}]`)),
    };
  });

  const chainIdRaw = raw.chainId;
  const chainId =
    chainIdRaw === undefined || chainIdRaw === null
      ? null
      : Number(intString(chainIdRaw, "chainId"));

  const signatureRaw = raw.signature;
  let signature: string | null = null;
  if (signatureRaw != null) {
    if (typeof signatureRaw !== "string" || !/^0x[0-9a-fA-F]{130}$/.test(signatureRaw)) {
      throw new Error("field signature must be 0x-prefixed 65-byte hex (130 chars)");
    }
    signature = signatureRaw;
  }

  return {
    protocol: typeof raw.protocol === "string" ? raw.protocol : "",
    strategyId: intString(raw.strategyId, "strategyId"),
    strategyName: typeof raw.strategyName === "string" ? raw.strategyName : "",
    epochIndex: intString(raw.epochIndex, "epochIndex"),
    committedAt: intString(raw.committedAt, "committedAt"),
    equityUsdg: intString(raw.equityUsdg, "equityUsdg"),
    netFlowUsdg: intString(raw.netFlowUsdg, "netFlowUsdg"),
    tradesRoot: hex32(raw.tradesRoot, "tradesRoot"),
    venue: Number(intString(raw.venue ?? 2, "venue")),
    instrument: typeof raw.instrument === "string" ? raw.instrument : "",
    receipts,
    signer: raw.signer == null ? null : addr(raw.signer, "signer"),
    chainId: chainId !== null && Number.isFinite(chainId) ? chainId : null,
    contract: raw.contract == null ? null : addr(raw.contract, "contract"),
    signature,
    signedAt: raw.signedAt == null ? null : intString(raw.signedAt, "signedAt"),
  };
}

function toReceiptInput(r: EvidenceReceiptEntry["receipt"]): ReceiptInput {
  return {
    strategyId: BigInt(r.strategyId),
    epochIndex: BigInt(r.epochIndex),
    venue: r.venue as Venue,
    venueOrderIdHash: r.venueOrderIdHash as Hash,
    instrumentHash: r.instrumentHash as Hash,
    side: r.side as Side,
    sizeSignedBase: BigInt(r.sizeSignedBase),
    priceQuote: BigInt(r.priceQuote),
    feeQuote: BigInt(r.feeQuote),
    filledAt: BigInt(r.filledAt),
  };
}

/**
 * Local, trustless checks per receipt: recompute the canonical leaf hash from
 * the receipt fields (the exact contract encoding), then walk the bundle's
 * Merkle proof against the bundle's tradesRoot. Anything that cannot be
 * recomputed reports hashOk=false rather than throwing.
 */
export function verifyBundleReceipts(bundle: EvidenceBundle): ReceiptCheck[] {
  return bundle.receipts.map((entry, index) => {
    let hashOk = false;
    let proofOk = false;
    try {
      const leaf = receiptHash(toReceiptInput(entry.receipt));
      hashOk = leaf.toLowerCase() === entry.hash.toLowerCase();
      proofOk = verifyProof(entry.hash as Hash, entry.proof as Hash[], bundle.tradesRoot as Hash);
    } catch {
      hashOk = false;
    }
    return { index, hash: entry.hash, hashOk, proofOk, ok: hashOk && proofOk };
  });
}

/** The exact EIP-712 shape the producer signs; must match byte-for-byte. */
const EVIDENCE_TYPES = {
  Evidence: [
    { name: "protocol", type: "string" },
    { name: "strategyId", type: "uint256" },
    { name: "epochIndex", type: "uint256" },
    { name: "committedAt", type: "uint64" },
    { name: "equityUsdg", type: "int256" },
    { name: "netFlowUsdg", type: "int256" },
    { name: "tradesRoot", type: "bytes32" },
  ],
} as const;

function typedDataOf(bundle: EvidenceBundle) {
  if (bundle.chainId === null || bundle.contract === null) {
    throw new Error("bundle carries no chainId/contract, so its signature domain cannot be rebuilt");
  }
  return {
    domain: {
      name: "Covenant Evidence",
      version: "1",
      chainId: bundle.chainId,
      verifyingContract: bundle.contract as Address,
    },
    types: EVIDENCE_TYPES,
    primaryType: "Evidence" as const,
    message: {
      protocol: bundle.protocol,
      strategyId: BigInt(bundle.strategyId),
      epochIndex: BigInt(bundle.epochIndex),
      committedAt: BigInt(bundle.committedAt),
      equityUsdg: BigInt(bundle.equityUsdg),
      netFlowUsdg: BigInt(bundle.netFlowUsdg),
      tradesRoot: bundle.tradesRoot as Hash,
    },
  };
}

/**
 * EIP-712 signature check: rebuild the producer's typed data, recover the
 * signer from the 65-byte rsv signature, and require it to equal the expected
 * identity. `expected` is the strategy owner read from getStrategy onchain
 * when the chain is configured; otherwise the bundle's own signer field is
 * used and the caller must label the check as self-declared.
 */
export async function verifyBundleSignature(
  bundle: EvidenceBundle,
  expected: string | null,
  expectedSource: SignatureCheck["expectedSource"],
): Promise<SignatureCheck> {
  if (bundle.signature === null) {
    return {
      present: false,
      ok: false,
      recovered: null,
      expected: null,
      expectedSource: "none",
      detail: "bundle carries no signature (v1 evidence format)",
    };
  }
  if (expected === null) {
    return {
      present: true,
      ok: false,
      recovered: null,
      expected: null,
      expectedSource,
      detail: "no expected signer available to compare against",
    };
  }
  try {
    const td = typedDataOf(bundle);
    const recovered = await recoverAddress({ hash: hashTypedData(td), signature: bundle.signature as `0x${string}` });
    const ok = await verifyTypedData({
      address: expected as Address,
      ...td,
      signature: bundle.signature as `0x${string}`,
    });
    return {
      present: true,
      ok,
      recovered,
      expected,
      expectedSource,
      detail: ok
        ? `signature recovers to ${recovered}, the expected signer`
        : `signature recovers to ${recovered}, not the expected ${expected}`,
    };
  } catch (e) {
    return {
      present: true,
      ok: false,
      recovered: null,
      expected,
      expectedSource,
      detail: `signature check failed: ${e instanceof Error ? e.message : String(e)}`,
    };
  }
}

/** 8-decimals fixed point (receipt price/fee/size) to a human decimal string. */
export function formatFixed8(v: string): string {
  const value = BigInt(v);
  const neg = value < 0n;
  const abs = neg ? -value : value;
  const whole = abs / 100_000_000n;
  const frac = (abs % 100_000_000n).toString().padStart(8, "0").replace(/0+$/, "");
  return `${neg ? "-" : ""}${whole.toString()}${frac === "" ? "" : `.${frac}`}`;
}

export function venueName(v: number): string {
  if (v === Venue.Coinbase) return "coinbase";
  if (v === Venue.ArbitrumDex) return "arbitrum-dex";
  return "other";
}

export function sideName(s: number): string {
  return s === Side.Buy ? "buy" : s === Side.Sell ? "sell" : `raw:${s}`;
}

/** Unix seconds to an ISO string; returns the raw value when out of range. */
export function unixToIso(seconds: string): string {
  const n = Number(seconds);
  if (!Number.isFinite(n)) return seconds;
  return new Date(n * 1000).toISOString();
}
