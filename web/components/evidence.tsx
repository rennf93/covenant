"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import CopyButton from "./copy-button";
import { formatUsdg } from "../lib/api";
import {
  formatFixed8,
  formatWitnessSummary,
  parseEvidenceBundle,
  sideName,
  unixToIso,
  venueName,
  verifyBundleReceipts,
  verifyBundleSignature,
  type EvidenceBundle,
  type SignatureCheck,
} from "../lib/evidence";

/**
 * Evidence chip upgrade: the chip tries to fetch the epoch's evidence bundle;
 * when the URI is actually served it gains an "inspect" action opening a
 * drawer that renders the bundle and verifies every receipt locally plus the
 * producer's EIP-712 signature. When the URI is not fetchable (the common
 * relative-path case) it degrades to today's copy-to-clipboard chip, so the
 * server-rendered markup is always the safe fallback.
 */

function EvidenceChip({
  uri,
  owner,
  strategyId,
}: {
  uri: string;
  owner: string;
  strategyId: string;
}) {
  const [bundle, setBundle] = useState<EvidenceBundle | null>(null);
  const [checked, setChecked] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const res = await fetch(uri, {
          headers: { accept: "application/json" },
          signal: AbortSignal.timeout(6000),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const parsed = parseEvidenceBundle(await res.json());
        if (!dead) {
          setBundle(parsed);
          setChecked(true);
        }
      } catch {
        if (!dead) setChecked(true);
      }
    })();
    return () => {
      dead = true;
    };
  }, [uri]);

  return (
    <span className="evidence-chip">
      <span className="evidence-uri" title={uri}>
        {uri}
      </span>
      {bundle !== null && (
        <button
          type="button"
          className="copy-chip inspect-chip"
          onClick={() => setOpen(true)}
          aria-label={`Open evidence bundle for strategy ${strategyId}: ${uri}`}
        >
          inspect
        </button>
      )}
      <CopyButton value={uri} />
      {checked && bundle !== null && open && (
        <EvidenceDrawer bundle={bundle} uri={uri} owner={owner} onClose={() => setOpen(false)} />
      )}
    </span>
  );
}

export default EvidenceChip;

/**
 * Full-screen glass drawer rendering one evidence bundle with its local
 * verification results. Dialog semantics: focus moves in, Tab wraps inside,
 * Escape closes, focus returns to the trigger.
 */
export function EvidenceDrawer({
  bundle,
  uri,
  owner,
  ownerSource = "chain-owner",
  onClose,
}: {
  bundle: EvidenceBundle;
  uri: string;
  owner: string | null;
  ownerSource?: "chain-owner" | "bundle-signer";
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const panel = panelRef.current;
    if (panel === null) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const focusables = panel.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0]!;
      const last = focusables[focusables.length - 1]!;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    const previousOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.documentElement.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, [onClose]);

  if (typeof document === "undefined") return null;
  return (
    <div
      className="drawer-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="panel drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`Evidence bundle for strategy ${bundle.strategyId}, epoch ${bundle.epochIndex}`}
      >
        <div className="panel-core">
          <div className="panel-head">
            <span>Evidence bundle</span>
            <span className="panel-head-tools">
              <span className="panel-meta">{uri}</span>
              <button
                ref={closeRef}
                type="button"
                className="copy-chip"
                onClick={onClose}
                aria-label="Close evidence drawer"
              >
                esc
              </button>
            </span>
          </div>
          <div className="drawer-body">
            <EvidenceBundleView bundle={bundle} owner={owner} ownerSource={ownerSource} />
          </div>
        </div>
      </div>
    </div>
  );
}

interface OwnerInfo {
  owner: string | null;
  source: "chain-owner" | "bundle-signer" | "none";
}

/**
 * Everything the drawer and the /verify evidence tab share: header fields,
 * the merkle + signature stamps, and the receipts table with per-receipt
 * verification marks.
 */
export function EvidenceBundleView({
  bundle,
  owner,
  ownerSource,
}: {
  bundle: EvidenceBundle;
  owner: string | null;
  ownerSource: OwnerInfo["source"];
}) {
  const receiptChecks = useMemo(() => verifyBundleReceipts(bundle), [bundle]);
  const [signature, setSignature] = useState<SignatureCheck | null>(null);

  useEffect(() => {
    let dead = false;
    verifyBundleSignature(bundle, owner, ownerSource).then((r) => {
      if (!dead) setSignature(r);
    });
    return () => {
      dead = true;
    };
  }, [bundle, owner, ownerSource]);

  const verifiedCount = receiptChecks.filter((c) => c.ok).length;
  const merkleState: "ok" | "bad" | "empty" =
    receiptChecks.length === 0 ? "empty" : verifiedCount === receiptChecks.length ? "ok" : "bad";

  return (
    <div className="ev">
      <div className="ev-fields">
        <div className="ev-field">
          <div className="stat-label">Strategy</div>
          <div className="ev-value">
            {bundle.strategyName !== "" ? `${bundle.strategyName} (#${bundle.strategyId})` : `#${bundle.strategyId}`}
          </div>
        </div>
        <div className="ev-field">
          <div className="stat-label">Epoch</div>
          <div className="ev-value">#{bundle.epochIndex}</div>
        </div>
        <div className="ev-field">
          <div className="stat-label">Equity (USDG)</div>
          <div className="ev-value">{formatUsdg(bundle.equityUsdg)}</div>
        </div>
        <div className="ev-field">
          <div className="stat-label">Net flow (USDG)</div>
          <div className="ev-value">{formatUsdg(bundle.netFlowUsdg)}</div>
        </div>
        <div className="ev-field">
          <div className="stat-label">Committed at</div>
          <div className="ev-value">{unixToIso(bundle.committedAt)}</div>
        </div>
        <div className="ev-field">
          <div className="stat-label">Instrument</div>
          <div className="ev-value">
            {bundle.instrument !== "" ? bundle.instrument : venueName(bundle.venue)}
          </div>
        </div>
        <div className="ev-field ev-field--wide">
          <div className="stat-label">Trades root</div>
          <div className="ev-value truncate" title={bundle.tradesRoot}>
            {bundle.tradesRoot}
          </div>
        </div>
        {bundle.signer !== null && (
          <div className="ev-field ev-field--wide">
            <div className="stat-label">Signer (claimed)</div>
            <div className="ev-value truncate" title={bundle.signer}>
              {bundle.signer}
            </div>
          </div>
        )}
        {bundle.signedAt !== null && (
          <div className="ev-field">
            <div className="stat-label">Signed at</div>
            <div className="ev-value">{unixToIso(bundle.signedAt)}</div>
          </div>
        )}
      </div>

      {bundle.witness !== null && (
        <div className="ev-witness">
          <div className="kv">
            <span>Pyth witness</span>
            <span>{formatWitnessSummary(bundle.witness)}</span>
          </div>
          <p className="honest-line">
            unsigned cross-check from a neutral price feed; verify against Pyth&apos;s historical
            API.
          </p>
        </div>
      )}

      <div className="stamp-row">
        <div
          className={`verdict compact ${merkleState === "ok" ? "ok" : merkleState === "bad" ? "bad" : ""}`}
          style={{ "--row-i": 0 } as CSSProperties}
          role="status"
        >
          <p className={`verdict-word ${merkleState === "empty" ? "dim" : ""}`}>
            {merkleState === "ok"
              ? "MERKLE VERIFIED"
              : merkleState === "bad"
                ? "MERKLE BROKEN"
                : "NO RECEIPTS"}
          </p>
          <p className="result">
            {merkleState === "empty"
              ? "the epoch committed an empty tree: nothing to walk locally."
              : `${verifiedCount}/${receiptChecks.length} receipts: leaf recomputed from fields and proof walked to the committed tradesRoot, locally.`}
          </p>
        </div>
        {signature === null ? (
          <div className="verdict compact" style={{ "--row-i": 1 } as CSSProperties} role="status">
            <p className="verdict-word dim">CHECKING SIGNATURE</p>
          </div>
        ) : !signature.present ? (
          <div className="verdict compact" style={{ "--row-i": 1 } as CSSProperties} role="status">
            <p className="verdict-word warn">UNSIGNED</p>
            <p className="result">{signature.detail}.</p>
          </div>
        ) : signature.ok ? (
          <div className="verdict compact ok" style={{ "--row-i": 1 } as CSSProperties} role="status">
            <p className="verdict-word">VERIFIED</p>
            <p className="result">{signature.detail}.</p>
          </div>
        ) : (
          <div className="verdict compact bad" style={{ "--row-i": 1 } as CSSProperties} role="status">
            <p className="verdict-word">SIGNATURE INVALID</p>
            <p className="result">{signature.detail}.</p>
          </div>
        )}
      </div>

      {ownerSource === "bundle-signer" && signature !== null && signature.present && (
        <p className="honest-line">
          Owner check is against the bundle&apos;s own signer field (the chain read is unavailable
          in this deployment), so this stamp proves self-consistency, not onchain ownership.
        </p>
      )}

      {receiptChecks.length > 0 ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Receipt hash</th>
                <th>Venue</th>
                <th>Side</th>
                <th className="num">Size (base)</th>
                <th className="num">Price (quote)</th>
                <th className="num">Fee (quote)</th>
                <th>Time</th>
                <th>Proof</th>
              </tr>
            </thead>
            <tbody>
              {bundle.receipts.map((entry, i) => {
                const check = receiptChecks[i];
                if (check === undefined) return null;
                const r = entry.receipt;
                return (
                  <tr key={entry.hash}>
                    <td data-label="Receipt hash">
                      <span className="truncate mono" title={entry.hash}>
                        {entry.hash}
                      </span>
                    </td>
                    <td data-label="Venue">{venueName(r.venue)}</td>
                    <td data-label="Side">
                      <span className={sideName(r.side) === "sell" ? "neg" : "pos"}>
                        {sideName(r.side)}
                      </span>
                    </td>
                    <td className="num" data-label="Size (base)">
                      {formatFixed8(r.sizeSignedBase)}
                    </td>
                    <td className="num" data-label="Price (quote)">
                      {formatFixed8(r.priceQuote)}
                    </td>
                    <td className="num" data-label="Fee (quote)">
                      {formatFixed8(r.feeQuote)}
                    </td>
                    <td className="mono dim" data-label="Time">
                      {unixToIso(r.filledAt)}
                    </td>
                    <td data-label="Proof">
                      {check.ok ? (
                        <span className="badge ok">leaf + proof ok</span>
                      ) : check.hashOk ? (
                        <span className="badge bad">proof broken</span>
                      ) : (
                        <span className="badge bad">leaf mismatch</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="honest-line">
          No receipts in this bundle: the epoch committed an empty tree, so there is nothing to
          walk locally.
        </p>
      )}
    </div>
  );
}
