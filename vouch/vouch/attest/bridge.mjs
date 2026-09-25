/**
 * Node bridge between the Python attestor and the Covenant Stylus contract.
 *
 * Protocol: reads exactly ONE JSON object from stdin, writes exactly ONE
 * JSON object to stdout. On any failure it writes {"error": "..."} to
 * stdout and exits nonzero. Nothing else may ever be printed to stdout
 * (Python parses stdout as the result payload). Every success response is
 * stamped with chainId so Python can pin EIP-712 domains without guessing.
 *
 * Payloads:
 *   {"action": "register", "name": ..., "metadataUri": ...}
 *   {"action": "commit", "strategyId", "epochIndex", "equity", "netFlow",
 *             "tradesRoot", "evidenceUri"}        (equity/netFlow decimal strings)
 *   {"action": "finalize", "strategyId", "epochIndex"}
 *   {"action": "verify", "strategyId", "epochIndex", "proof": [...], "receiptHash"}
 *   {"action": "getPerformance", "strategyId"}     (view: epochCount + PnL)
 *   {"action": "getStrategy", "strategyId"}        (view)
 *   {"action": "getCheckpoint", "strategyId", "epochIndex"}  (view)
 *   {"action": "getConfig"}                        (view: challengeWindow etc.)
 *   {"action": "sign_evidence", "typedData"}       (local EIP-712 signing, no tx)
 *
 * View actions work without COVENANT_PRIVATE_KEY; everything else (and
 * sign_evidence) requires it.
 *
 * Env: COVENANT_RPC_URL, COVENANT_CONTRACT_ADDRESS required. COVENANT_CHAIN:
 * "arbitrum-sepolia" (default) | "arbitrum" | "anvil".
 *
 * Module resolution: the repo root has no node_modules; viem lives in the
 * sdk package, so the require is anchored at ../../../sdk/package.json
 * (three levels up: vouch/vouch/attest/bridge.mjs -> covenant/sdk/).
 */

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const req = createRequire(new URL("../../../sdk/package.json", import.meta.url));

const { createPublicClient, createWalletClient, http } = req("viem");
const chains = req("viem/chains");
const { privateKeyToAccount } = req("viem/accounts");
const { covenantAbi } = await import(
  new URL("../../../sdk/dist/src/abi.js", import.meta.url).href
);

const CHAINS = {
  "arbitrum-sepolia": chains.arbitrumSepolia,
  arbitrum: chains.arbitrum,
  anvil: chains.anvil,
};

const ACTIONS = [
  "register",
  "commit",
  "finalize",
  "verify",
  "getPerformance",
  "getStrategy",
  "getCheckpoint",
  "getConfig",
  "sign_evidence",
];

function fail(message) {
  process.stdout.write(JSON.stringify({ error: message }) + "\n");
  process.exit(1);
}

function env(name) {
  const v = process.env[name];
  return v === undefined ? "" : v.trim();
}

function readStdin() {
  try {
    return readFileSync(0, "utf8");
  } catch (e) {
    return fail(`failed to read stdin: ${e.message}`);
  }
}

function requireFields(payload, fields) {
  for (const f of fields) {
    if (payload[f] === undefined || payload[f] === null) {
      fail(`missing field: ${f}`);
    }
  }
}

function toBigInt(v) {
  return typeof v === "bigint" ? v : BigInt(String(v));
}

/** bigint -> decimal string so JSON stays lossless. */
function s(v) {
  return String(v);
}

async function main() {
  const raw = readStdin();
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (e) {
    return fail(`stdin is not valid JSON: ${e.message}`);
  }
  const action = payload.action;
  if (!ACTIONS.includes(action)) {
    return fail(`unknown action: ${String(action)}`);
  }

  const rpcUrl = env("COVENANT_RPC_URL");
  const contractAddress = env("COVENANT_CONTRACT_ADDRESS");
  const chainName = env("COVENANT_CHAIN") || "arbitrum-sepolia";
  const chain = CHAINS[chainName];
  if (!chain) {
    return fail(`unknown COVENANT_CHAIN: ${chainName}`);
  }
  if (!rpcUrl) {
    return fail("COVENANT_RPC_URL is required");
  }
  if (!contractAddress) {
    return fail("COVENANT_CONTRACT_ADDRESS is required");
  }

  const needsKey = action !== "verify" && !action.startsWith("get");
  const privateKey = env("COVENANT_PRIVATE_KEY");
  let account = null;
  if (needsKey) {
    if (!privateKey) {
      return fail(`COVENANT_PRIVATE_KEY is required for action "${action}"`);
    }
    const pk = privateKey.startsWith("0x") ? privateKey : `0x${privateKey}`;
    try {
      account = privateKeyToAccount(pk);
    } catch (e) {
      return fail(`invalid COVENANT_PRIVATE_KEY: ${e.message}`);
    }
  }

  const transport = http(rpcUrl);
  const publicClient = createPublicClient({ chain, transport });
  const walletClient = account
    ? createWalletClient({ account, chain, transport })
    : null;

  // Success wrapper: every response carries the chain id so the Python side
  // can pin the EIP-712 domain to the chain it will commit on.
  const ok = (result) => ({ ...result, chainId: chain.id });

  try {
    if (action === "register") {
      requireFields(payload, ["name", "metadataUri"]);
      const hash = await walletClient.writeContract({
        address: contractAddress,
        abi: covenantAbi,
        functionName: "registerStrategy",
        args: [String(payload.name), String(payload.metadataUri)],
      });
      const receipt = await publicClient.waitForTransactionReceipt({ hash });
      const result = { hash };
      // strategyId from the StrategyRegistered log: topic1 is the indexed
      // uint256 strategy_id (topic0 = event signature, topic2 = owner).
      const log = receipt.logs.find(
        (l) =>
          l.address.toLowerCase() === contractAddress.toLowerCase() &&
          l.topics.length >= 3 &&
          l.topics[1],
      );
      if (log && log.topics[1]) {
        try {
          result.strategyId = BigInt(log.topics[1]).toString();
        } catch {
          // omit rather than guess
        }
      }
      return ok(result);
    }

    if (action === "commit") {
      requireFields(payload, [
        "strategyId",
        "epochIndex",
        "equity",
        "netFlow",
        "tradesRoot",
        "evidenceUri",
      ]);
      const hash = await walletClient.writeContract({
        address: contractAddress,
        abi: covenantAbi,
        functionName: "commitEpoch",
        args: [
          toBigInt(payload.strategyId),
          toBigInt(payload.epochIndex),
          BigInt(String(payload.equity)), // decimal string, may be negative
          BigInt(String(payload.netFlow)), // decimal string, may be negative
          payload.tradesRoot,
          String(payload.evidenceUri),
        ],
      });
      const receipt = await publicClient.waitForTransactionReceipt({ hash });
      return ok({ hash, blockNumber: s(receipt.blockNumber) });
    }

    if (action === "finalize") {
      requireFields(payload, ["strategyId", "epochIndex"]);
      const hash = await walletClient.writeContract({
        address: contractAddress,
        abi: covenantAbi,
        functionName: "finalizeEpoch",
        args: [toBigInt(payload.strategyId), toBigInt(payload.epochIndex)],
      });
      const receipt = await publicClient.waitForTransactionReceipt({ hash });
      return ok({ hash, blockNumber: s(receipt.blockNumber) });
    }

    if (action === "getPerformance") {
      requireFields(payload, ["strategyId"]);
      const rawPerf = await publicClient.readContract({
        address: contractAddress,
        abi: covenantAbi,
        functionName: "getPerformance",
        args: [toBigInt(payload.strategyId)],
      });
      const [equity, highWaterMark, cumulativePnl, epochCount, finalizedEpochs] = rawPerf;
      return ok({
        equity: s(equity),
        highWaterMark: s(highWaterMark),
        cumulativePnl: s(cumulativePnl),
        epochCount: s(epochCount),
        finalizedEpochs: s(finalizedEpochs),
      });
    }

    if (action === "getStrategy") {
      requireFields(payload, ["strategyId"]);
      const rawStrategy = await publicClient.readContract({
        address: contractAddress,
        abi: covenantAbi,
        functionName: "getStrategy",
        args: [toBigInt(payload.strategyId)],
      });
      const [owner, name, metadataUri, bond, status, createdAt] = rawStrategy;
      return ok({
        owner,
        name,
        metadataUri,
        bond: s(bond),
        status: Number(status),
        createdAt: s(createdAt),
      });
    }

    if (action === "getCheckpoint") {
      requireFields(payload, ["strategyId", "epochIndex"]);
      const rawCp = await publicClient.readContract({
        address: contractAddress,
        abi: covenantAbi,
        functionName: "getCheckpoint",
        args: [toBigInt(payload.strategyId), toBigInt(payload.epochIndex)],
      });
      const [equity, netFlow, tradesRoot, evidenceUri, status, committedAt, challenger, stake] =
        rawCp;
      return ok({
        equity: s(equity),
        netFlow: s(netFlow),
        tradesRoot,
        evidenceUri,
        status: Number(status),
        committedAt: s(committedAt),
        challenger,
        stake: s(stake),
      });
    }

    if (action === "getConfig") {
      const rawCfg = await publicClient.readContract({
        address: contractAddress,
        abi: covenantAbi,
        functionName: "config",
      });
      const [admin, resolver, usdg, bondAmount, challengeStake, challengeWindow, paused, treasury] =
        rawCfg;
      return ok({
        admin,
        resolver,
        usdg,
        bondAmount: s(bondAmount),
        challengeStake: s(challengeStake),
        challengeWindow: s(challengeWindow),
        paused: Boolean(paused),
        treasury: s(treasury),
      });
    }

    if (action === "sign_evidence") {
      requireFields(payload, ["typedData"]);
      const td = payload.typedData;
      requireFields(td, ["domain", "types", "message"]);
      const signature = await account.signTypedData({
        domain: td.domain,
        types: td.types,
        primaryType: td.primaryType || "Evidence",
        message: td.message,
      });
      return ok({ signature, signer: account.address });
    }

    // action === "verify" (read; no wallet needed)
    requireFields(payload, ["strategyId", "epochIndex", "proof", "receiptHash"]);
    const valid = await publicClient.readContract({
      address: contractAddress,
      abi: covenantAbi,
      functionName: "verifyReceipt",
      args: [
        toBigInt(payload.strategyId),
        toBigInt(payload.epochIndex),
        payload.proof,
        payload.receiptHash,
      ],
    });
    return ok({ valid: Boolean(valid) });
  } catch (e) {
    return fail(`${action} failed: ${e.shortMessage || e.message}`);
  }
}

main()
  .then((result) => {
    if (result !== undefined) {
      process.stdout.write(JSON.stringify(result) + "\n");
    }
  })
  .catch((e) => fail(`bridge failed: ${e.shortMessage || e.message}`));
