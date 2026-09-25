"use client";

import { useState } from "react";
import { createPublicClient, http, type Address } from "viem";
import { covenantAbi } from "@covenant/sdk";
import { chainName, configuredChain, contractAddress, rpcUrl } from "../../lib/chain";

/**
 * Client-side onchain verification: calls the contract's verifyReceipt view
 * directly, so a visitor can audit a disclosed receipt without trusting this
 * website's backend at all.
 */

export default function VerifyClient() {
  const [strategyId, setStrategyId] = useState("1");
  const [epochIndex, setEpochIndex] = useState("0");
  const [receipt, setReceipt] = useState("");
  const [proofText, setProofText] = useState("");
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      const contract = contractAddress();
      const rpc = rpcUrl();
      const chain = configuredChain();
      if (contract === null || rpc === "" || chain === null) {
        throw new Error(
          `NEXT_PUBLIC_CONTRACT_ADDRESS / NEXT_PUBLIC_RPC_URL / NEXT_PUBLIC_CHAIN are not configured (chain: ${chainName()})`,
        );
      }
      if (!/^0x[0-9a-fA-F]{64}$/.test(receipt.trim())) {
        throw new Error("receipt must be a 32-byte hex hash (0x…, 64 hex chars)");
      }
      const proof = proofText
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l.length > 0)
        .map((l) => {
          if (!/^0x[0-9a-fA-F]{64}$/.test(l)) {
            throw new Error(`proof line is not a 32-byte hex hash: ${l.slice(0, 20)}…`);
          }
          return l as `0x${string}`;
        });

      const client = createPublicClient({ chain, transport: http(rpc) });
      const verified = (await client.readContract({
        address: contract,
        abi: covenantAbi,
        functionName: "verifyReceipt",
        args: [BigInt(strategyId), BigInt(epochIndex), proof, receipt.trim() as `0x${string}`],
      })) as boolean;
      setResult({
        ok: verified,
        text: verified
          ? `VERIFIED - leaf ${receipt.trim()} is committed in the strategy's epoch history.`
          : `REJECTED - leaf ${receipt.trim()} is not part of the committed epoch history.`,
      });
    } catch (err) {
      setResult({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="verify" onSubmit={onSubmit}>
        <div className="field">
          <label className="field-label" htmlFor="strategy-id">
            Strategy id
          </label>
          <input
            id="strategy-id"
            value={strategyId}
            onChange={(e) => setStrategyId(e.target.value)}
            inputMode="numeric"
          />
          <p className="field-helper">numeric strategy registry id</p>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="epoch-index">
            Epoch index
          </label>
          <input
            id="epoch-index"
            value={epochIndex}
            onChange={(e) => setEpochIndex(e.target.value)}
            inputMode="numeric"
          />
          <p className="field-helper">epoch index within the strategy&apos;s history</p>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="receipt-hash">
            Receipt hash
          </label>
          <input
            id="receipt-hash"
            value={receipt}
            onChange={(e) => setReceipt(e.target.value)}
            placeholder="0x…"
          />
          <p className="field-helper">32-byte hex hash (0x…, 64 hex chars) from the evidence bundle</p>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="merkle-proof">
            Merkle proof
          </label>
          <textarea
            id="merkle-proof"
            value={proofText}
            onChange={(e) => setProofText(e.target.value)}
            placeholder={"0xaaaa…\n0xbbbb…"}
          />
          <p className="field-helper">one sibling hash per line, 32-byte hex each</p>
        </div>
        {busy && (
          <div className="scan-bar" role="status" aria-label="Checking onchain" />
        )}
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Checking onchain…" : "Verify onchain"}
        </button>
      </form>
      {result !== null && (
        <div className={`verdict ${result.ok ? "ok" : "bad"}`} role="status">
          <p className="verdict-word">{result.ok ? "VERIFIED" : "REJECTED"}</p>
          <p className="result">{result.text}</p>
        </div>
      )}
    </>
  );
}
