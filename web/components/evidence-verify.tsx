"use client";

import { useEffect, useState } from "react";
import { EvidenceBundleView } from "./evidence";
import { formatWitnessLine, parseEvidenceBundle, verifyBundleReceipts, verifyBundleSignature, type EvidenceBundle } from "../lib/evidence";
import { readStrategyOwner } from "../lib/chain";

/**
 * The /verify page's second tab: load a signed evidence bundle from a URL or
 * pasted JSON, then run the same checks the drawer runs (schema, per-receipt
 * Merkle proofs against the bundle tradesRoot, EIP-712 signature against the
 * strategy owner) before anyone scrolls a single receipt.
 */
export default function EvidenceVerify() {
  const [url, setUrl] = useState("");
  const [pasted, setPasted] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bundle, setBundle] = useState<EvidenceBundle | null>(null);
  const [ownerInfo, setOwnerInfo] = useState<{ owner: string | null; source: "chain-owner" | "bundle-signer" | "none" } | null>(null);
  const [checks, setChecks] = useState<{ ok: boolean; line: string } | null>(null);

  useEffect(() => {
    if (bundle === null || ownerInfo === null) return;
    let dead = false;
    (async () => {
      const receiptChecks = verifyBundleReceipts(bundle);
      const verified = receiptChecks.filter((c) => c.ok).length;
      const signature = await verifyBundleSignature(bundle, ownerInfo.owner, ownerInfo.source);
      if (dead) return;
      const ok =
        receiptChecks.length > 0 && verified === receiptChecks.length && signature.present && signature.ok;
      const lines = [
        `schema: parsed as a covenant evidence bundle (strategy ${bundle.strategyId}, epoch ${bundle.epochIndex})`,
        `merkle: ${verified}/${receiptChecks.length} receipts verify against tradesRoot ${bundle.tradesRoot}`,
        `signature: ${signature.detail}`,
        ownerInfo.source === "chain-owner"
          ? "expected signer: strategy owner from getStrategy onchain"
          : ownerInfo.source === "bundle-signer"
            ? "expected signer: the bundle's own signer field (chain read unavailable)"
            : "expected signer: none available",
      ];
      if (bundle.witness !== null) {
        lines.push(`witness: ${formatWitnessLine(bundle.witness)} (unsigned cross-check)`);
      }
      setChecks({ ok, line: lines.join("\n") });
    })();
    return () => {
      dead = true;
    };
  }, [bundle, ownerInfo]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setBundle(null);
    setChecks(null);
    setOwnerInfo(null);
    try {
      let raw: unknown;
      const text = pasted.trim();
      const target = url.trim();
      if (text !== "") {
        raw = JSON.parse(text);
      } else if (target !== "") {
        const res = await fetch(target, {
          headers: { accept: "application/json" },
          signal: AbortSignal.timeout(10000),
        });
        if (!res.ok) throw new Error(`fetch failed: HTTP ${res.status}`);
        raw = await res.json();
      } else {
        throw new Error("paste bundle JSON or give a bundle URL first");
      }
      const parsed = parseEvidenceBundle(raw);
      const chainOwner = await readStrategyOwner(BigInt(parsed.strategyId));
      if (chainOwner !== null) {
        setOwnerInfo({ owner: chainOwner, source: "chain-owner" });
      } else if (parsed.signer !== null) {
        setOwnerInfo({ owner: parsed.signer, source: "bundle-signer" });
      } else {
        setOwnerInfo({ owner: null, source: "none" });
      }
      setBundle(parsed);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="verify" onSubmit={onSubmit}>
        <div className="field">
          <label className="field-label" htmlFor="evidence-url">
            Bundle URL
          </label>
          <input
            id="evidence-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…/epoch-7.json"
            type="url"
          />
          <p className="field-helper">fetched straight from your browser (CORS applies)</p>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="evidence-json">
            or paste bundle JSON
          </label>
          <textarea
            id="evidence-json"
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            placeholder={`{\n  "protocol": "covenant-v1",\n  "strategyId": …\n}`}
          />
          <p className="field-helper">takes precedence when both are filled</p>
        </div>
        {busy && (
          <div className="scan-bar" role="status" aria-label="Verifying bundle" />
        )}
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Verifying bundle…" : "Verify bundle"}
        </button>
      </form>
      {error !== null && (
        <div className="verdict bad" role="status">
          <p className="verdict-word">INVALID BUNDLE</p>
          <p className="result">{error}</p>
        </div>
      )}
      {bundle !== null && ownerInfo !== null && (
        <EvidenceBundleView bundle={bundle} owner={ownerInfo.owner} ownerSource={ownerInfo.source} />
      )}
      {checks !== null && (
        <div className={`verdict ${checks.ok ? "ok" : "bad"}`} role="status">
          <p className="verdict-word">{checks.ok ? "VERIFIED" : "REJECTED"}</p>
          <p className="result" style={{ whiteSpace: "pre-line" }}>
            {checks.line}
          </p>
        </div>
      )}
    </>
  );
}
