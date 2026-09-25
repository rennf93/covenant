import { decodeErrorResult, toFunctionSelector, type Abi, type AbiParameter } from "viem";
import { covenantAbi } from "./abi.js";

/**
 * Revert decoding for Covenant clients. Custom error selectors are matched
 * against the ABI's `error` entries generically, so any error the contract
 * adds (and abi.ts regenerates) decodes with no code change here.
 */

export interface DecodedError {
  /** Custom error name, e.g. "EpochNotPending"; the error's class name when unmatched. */
  name: string;
  /** 4-byte selector of the revert payload; null when no revert data was found. */
  selector: `0x${string}` | null;
  /** Decoded error args; empty when they could not be decoded. */
  args: Record<string, unknown>;
  /** Raw revert payload the decode came from, when one was found. */
  data: `0x${string}` | null;
  /** True when the selector matched an `error` entry of the ABI. */
  matched: boolean;
}

/**
 * Walks a viem error chain collecting candidate revert payloads
 * (getContractError-compatible: RawContractError, CallExecutionError, plain
 * `{ data }` shapes, and `data.data` nestings).
 */
export function revertData(err: unknown): `0x${string}` | null {
  const seen = new Set<unknown>();
  const stack: unknown[] = [err];
  while (stack.length > 0) {
    const current = stack.pop();
    if (current === null || current === undefined || typeof current !== "object") continue;
    if (seen.has(current)) continue;
    seen.add(current);
    const record = current as { data?: unknown; cause?: unknown; error?: unknown };
    const nested = record.data as { data?: unknown } | null | undefined;
    for (const candidate of [record.data, nested?.data]) {
      if (typeof candidate === "string" && /^0x[0-9a-fA-F]*$/.test(candidate) && candidate.length >= 10) {
        return candidate as `0x${string}`;
      }
    }
    stack.push(record.cause, record.error);
  }
  return null;
}

/** Canonical ABI type string (tuples recurse, array suffixes survive). */
function canonicalType(input: { type: string; components?: readonly AbiParameter[] }): string {
  if (input.type.startsWith("tuple")) {
    const suffix = input.type.slice("tuple".length);
    const inner = (input.components ?? []).map(canonicalType).join(",");
    return `(${inner})${suffix}`;
  }
  return input.type;
}

/** Canonical signature of one ABI error entry, e.g. "EpochNotPending()". */
function errorSignature(item: { name: string; inputs?: readonly AbiParameter[] | undefined }): string {
  return `${item.name}(${(item.inputs ?? []).map(canonicalType).join(",")})`;
}

/** 4-byte selector of one ABI error entry (errors select exactly like functions). */
export function errorSelector(item: unknown): `0x${string}` | null {
  const entry = item as { type?: string; name?: string; inputs?: readonly AbiParameter[] };
  if (entry.type !== "error" || typeof entry.name !== "string") return null;
  try {
    return toFunctionSelector(errorSignature({ name: entry.name, inputs: entry.inputs }));
  } catch {
    return null;
  }
}

const bigintStringify = (_key: string, value: unknown): unknown =>
  typeof value === "bigint" ? value.toString() : value;

/** Decodes a thrown RPC error into a structured revert description. */
export function decodeError(err: unknown, abi: Abi = covenantAbi): DecodedError {
  const data = revertData(err);
  const fallback: DecodedError = {
    name: err instanceof Error ? err.name : "UnknownError",
    selector: data === null ? null : (data.slice(0, 10) as `0x${string}`),
    args: {},
    data,
    matched: false,
  };
  if (data === null) return fallback;
  const selector = data.slice(0, 10) as `0x${string}`;
  try {
    const decoded = decodeErrorResult({ abi, data });
    let args: unknown = decoded.args ?? {};
    if (Array.isArray(args)) {
      // viem decodes positionally for errors; name the args from the ABI item
      // when every input is named, so callers get { strategy_id: 7n } shapes.
      const positional = args as unknown[];
      const item = abi.find(
        (entry): entry is Extract<Abi[number], { type: "error" }> =>
          entry.type === "error" && errorSelector(entry) === selector,
      );
      const inputs = item?.inputs ?? [];
      if (inputs.length === positional.length && inputs.every((input) => input.name !== "")) {
        args = Object.fromEntries(inputs.map((input, index) => [input.name, positional[index]]));
      }
    }
    return {
      name: decoded.errorName,
      selector,
      args: args as Record<string, unknown>,
      data,
      matched: true,
    };
  } catch {
    // Not in the ABI (stale abi.ts, or a token error): fall through to the
    // manual selector table, which never throws.
  }
  for (const item of abi) {
    if (errorSelector(item) === selector) {
      return { name: (item as { name: string }).name, selector, args: {}, data, matched: true };
    }
  }
  return fallback;
}

/** Error thrown by the covenant clients when a revert decodes against the ABI. */
export class CovenantError extends Error {
  readonly decoded: DecodedError;

  constructor(decoded: DecodedError, options?: { cause?: unknown }) {
    const args = Object.keys(decoded.args).length > 0 ? ` ${JSON.stringify(decoded.args, bigintStringify)}` : "";
    super(`contract reverted: ${decoded.name}${args}`, options);
    this.name = "CovenantError";
    this.decoded = decoded;
  }
}

/**
 * Rethrows `err` as a CovenantError when its revert decodes against the ABI;
 * anything else (network errors, unknown selectors) passes through untouched.
 */
export function wrapContractError(err: unknown, abi: Abi = covenantAbi): unknown {
  const decoded = decodeError(err, abi);
  return decoded.matched ? new CovenantError(decoded, { cause: err }) : err;
}
