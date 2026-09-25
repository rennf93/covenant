"use client";

import ConnectWallet from "./connect-wallet";
import { useChallenge } from "./challenge-provider";
import { formatUsdg } from "../lib/api";

/**
 * The challenge game's action area on a strategy page: wallet connection,
 * the live contract parameters the game runs on, and the economics in plain
 * words. In demo mode (nothing deployed) it says exactly that instead of
 * pretending a game exists.
 */

function humanWindow(sec: bigint): string {
  const s = Number(sec);
  if (!Number.isFinite(s) || s < 0) return `${sec}s`;
  if (s >= 86400 && s % 86400 === 0) return `${s / 86400}d`;
  if (s >= 3600 && s % 3600 === 0) return `${s / 3600}h`;
  if (s >= 60 && s % 60 === 0) return `${s / 60}m`;
  return `${s}s`;
}

export default function ChallengeDesk() {
  const { demo, config, configError, address, chainOk } = useChallenge();

  return (
    <div className="panel cta-panel" aria-label="Challenge game">
      <div className="panel-core">
        <div className="panel-head">
          <span>Challenge game</span>
          <span className="panel-head-tools">
            {config !== null && config.paused && <span className="badge warn">paused</span>}
            <ConnectWallet />
          </span>
        </div>
        <div className="panel-body">
          {demo ? (
            <>
              <span className="warn">CONTRACT NOT DEPLOYED</span>
              <br />
              Demo data only: no Covenant contract is configured
              (NEXT_PUBLIC_CONTRACT_ADDRESS is unset), so there is nothing to
              stake against. The challenge game activates once the contract is
              deployed and wired into the site.
            </>
          ) : (
            <>
              <div className="desk-facts">
                <span className="kv">
                  <span>challenge stake</span>
                  <span>
                    {config === null ? "reading…" : `${formatUsdg(config.challengeStake.toString())} USDG`}
                  </span>
                </span>
                <span className="kv">
                  <span>challenge window</span>
                  <span>{config === null ? "reading…" : humanWindow(config.challengeWindow)}</span>
                </span>
                <span className="kv">
                  <span>resolver</span>
                  <span className="truncate" title={config?.resolver}>
                    {config === null ? "reading…" : config.resolver}
                  </span>
                </span>
                {config !== null && address !== null && address.toLowerCase() === config.resolver.toLowerCase() && (
                  <span className="kv">
                    <span>you</span>
                    <span className="pos">are the resolver</span>
                  </span>
                )}
              </div>
              {configError !== null && (
                <p className="status-line neg" role="status">
                  contract read failed: {configError}
                </p>
              )}
              <p className="honest-line">
                Economics, honestly: filing a challenge escrows the stake. If
                the resolver dismisses, your stake is forfeited to the treasury.
                If it upholds, the epoch is invalidated and the operator&apos;s
                bond is slashed to you.
              </p>
              {config !== null && config.paused && (
                <p className="status-line warn" role="status">
                  contract is paused: every write (challenge and resolve) is
                  disabled until it unpauses.
                </p>
              )}
              {address !== null && !chainOk && (
                <p className="status-line warn" role="status">
                  wallet is on the wrong network: switch it to file or resolve.
                </p>
              )}
              {address === null && (
                <p className="status-line dim" role="status">
                  connect a wallet to challenge a pending epoch (or to resolve,
                  if you are the resolver).
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
