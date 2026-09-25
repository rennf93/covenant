"use client";

import { useEffect, useState } from "react";
import { useChallenge } from "./challenge-provider";

/**
 * The single wallet affordance: connect on click, trimmed address with a
 * disconnect action once connected, and a wrong-network nudge that asks the
 * wallet to switch to NEXT_PUBLIC_CHAIN. Renders nothing in demo mode (the
 * desk panel explains why) and a quiet "no wallet" chip when no injected
 * provider exists.
 */

function shortAddr(addr: string): string {
  return addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr;
}

export default function ConnectWallet() {
  const {
    demo,
    address,
    chainOk,
    chainLabel,
    connect,
    disconnect,
    switchToCovenant,
    walletError,
  } = useChallenge();
  const [hasWallet, setHasWallet] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setHasWallet(window.ethereum !== undefined);
  }, []);

  if (demo) return null;

  if (address === null) {
    return (
      <span className="connect-wallet">
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={() => {
            setBusy(true);
            void connect().finally(() => setBusy(false));
          }}
          disabled={busy === true || hasWallet === false}
          aria-label="Connect an injected wallet"
        >
          {busy ? "connecting…" : "Connect wallet"}
        </button>
        {hasWallet === false && <span className="dim mono">no wallet in browser</span>}
        {walletError !== null && (
          <span className="status-line neg" role="status">
            {walletError}
          </span>
        )}
      </span>
    );
  }

  return (
    <span className="connect-wallet">
      <span className="chip" title={address}>
        <span className="dot dot-ok" aria-hidden="true" />
        {shortAddr(address)}
      </span>
      <button
        type="button"
        className="copy-chip"
        onClick={disconnect}
        aria-label={`Disconnect wallet ${address}`}
      >
        disconnect
      </button>
      {!chainOk && (
        <>
          <span className="badge warn">wrong network</span>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => void switchToCovenant()}
          >
            Switch to {chainLabel}
          </button>
        </>
      )}
    </span>
  );
}
