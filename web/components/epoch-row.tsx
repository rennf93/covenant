"use client";

import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { covenantAbi, erc20Abi } from "@covenant/sdk";
import { formatUsdg } from "../lib/api";
import { txErrorMessage } from "../lib/chain";
import { useChallenge } from "./challenge-provider";

/**
 * Per-epoch challenge-game controls rendered inside the epoch table:
 * a Challenge button for pending epochs (opening an inline glass panel with
 * the approve + challengeEpoch flow) and, for the connected resolver,
 * Uphold/Dismiss buttons with a confirm step on challenged epochs. Every
 * failure surfaces the decoded revert reason; every success refreshes the
 * server-rendered row.
 */

type Phase = "idle" | "approving" | "challenging" | "confirming" | "done" | "error";

interface EpochRowProps {
  strategyId: string;
  epochIndex: string;
  status: number;
  challenger: string | null;
  stake: string | null;
  rowClass: string;
  style: CSSProperties;
  /** Total table columns, for the inline panel's colSpan. */
  colCount: number;
  /** The server-rendered cells of this row. */
  children: ReactNode;
}

function shortHash(hash: string): string {
  return hash.length > 18 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash;
}

export default function EpochRow({
  strategyId,
  epochIndex,
  status,
  challenger,
  stake,
  rowClass,
  style,
  colCount,
  children,
}: EpochRowProps) {
  const { demo } = useChallenge();
  const [open, setOpen] = useState(false);

  return (
    <>
      <tr className={rowClass} style={style}>
        {children}
        <td data-label="Game">
          {demo ? (
            <span className="dim" title="contract not deployed">
              -
            </span>
          ) : status === 0 ? (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              aria-expanded={open}
              aria-controls={`challenge-panel-${strategyId}-${epochIndex}`}
              onClick={() => setOpen((o) => !o)}
            >
              {open ? "Close" : "Challenge"}
            </button>
          ) : status === 2 ? (
            <ResolverActions
              strategyId={strategyId}
              epochIndex={epochIndex}
              challenger={challenger}
              stake={stake}
            />
          ) : (
            <span className="dim">-</span>
          )}
        </td>
      </tr>
      {open && status === 0 && !demo && (
        <tr className="panel-row">
          <td colSpan={colCount} data-label={`Challenge epoch #${epochIndex}`}>
            <ChallengePanel
              strategyId={strategyId}
              epochIndex={epochIndex}
              panelId={`challenge-panel-${strategyId}-${epochIndex}`}
              onClose={() => setOpen(false)}
            />
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Inline glass panel for filing a challenge: shows the stake, the wallet's
 * USDG balance and allowance, walks approve then challengeEpoch, and reports
 * each step in a mono status line.
 */
function ChallengePanel({
  strategyId,
  epochIndex,
  panelId,
  onClose,
}: {
  strategyId: string;
  epochIndex: string;
  panelId: string;
  onClose: () => void;
}) {
  const router = useRouter();
  const { contract, config, configError, publicClient, wallet, address, chainOk, refreshConfig } =
    useChallenge();

  const [reason, setReason] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [errorLine, setErrorLine] = useState<string | null>(null);
  const [balance, setBalance] = useState<bigint | null>(null);
  const [allowance, setAllowance] = useState<bigint | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [readTick, setReadTick] = useState(0);

  const stake = config?.challengeStake ?? null;
  const paused = config?.paused ?? false;
  const writable =
    contract !== null && wallet !== null && address !== null && chainOk && config !== null && !paused;

  // Balance + allowance reads, refreshed after an approval.
  useEffect(() => {
    if (config === null || address === null || contract === null || publicClient === null) return;
    let dead = false;
    (async () => {
      try {
        const [bal, allow] = await Promise.all([
          publicClient.readContract({
            address: config.usdg,
            abi: erc20Abi,
            functionName: "balanceOf",
            args: [address],
          }),
          publicClient.readContract({
            address: config.usdg,
            abi: erc20Abi,
            functionName: "allowance",
            args: [address, contract],
          }),
        ]);
        if (dead) return;
        setBalance(bal);
        setAllowance(allow);
        setReadError(null);
      } catch (e) {
        if (!dead) setReadError(txErrorMessage(e));
      }
    })();
    return () => {
      dead = true;
    };
  }, [config, address, contract, publicClient, readTick]);

  const needsApprove = stake !== null && allowance !== null && allowance < stake;
  const reasonOk = reason.trim().length > 0;
  const busy = phase === "approving" || phase === "challenging" || phase === "confirming";

  async function runChallenge() {
    if (contract === null || wallet === null || address === null || publicClient === null) return;
    if (stake === null || config === null) return;
    setErrorLine(null);
    try {
      let currentAllowance = allowance;
      if (needsApprove) {
        setPhase("approving");
        setStatusLine(`approve ${formatUsdg(stake.toString())} USDG to the covenant contract…`);
        const approveHash = await wallet.writeContract({
          address: config.usdg,
          abi: erc20Abi,
          functionName: "approve",
          args: [contract, stake],
          account: address,
          chain: wallet.chain,
        });
        setPhase("confirming");
        setStatusLine(`approve ${shortHash(approveHash)} - waiting for confirmation…`);
        await publicClient.waitForTransactionReceipt({ hash: approveHash });
        currentAllowance = await publicClient.readContract({
          address: config.usdg,
          abi: erc20Abi,
          functionName: "allowance",
          args: [address, contract],
        });
        setAllowance(currentAllowance);
        setReadTick((t) => t + 1);
      }
      setPhase("challenging");
      setStatusLine(
        `challengeEpoch(strategy ${strategyId}, epoch ${epochIndex}) - confirm in your wallet…`,
      );
      const hash = await wallet.writeContract({
        address: contract,
        abi: covenantAbi,
        functionName: "challengeEpoch",
        args: [BigInt(strategyId), BigInt(epochIndex), reason.trim()],
        account: address,
        chain: wallet.chain,
      });
      setPhase("confirming");
      setStatusLine(`challenge ${shortHash(hash)} - waiting for confirmation…`);
      await publicClient.waitForTransactionReceipt({ hash });
      setPhase("done");
      setStatusLine(
        `challenge filed: epoch #${epochIndex} is now challenged and your stake is escrowed until the resolver rules.`,
      );
      refreshConfig();
      router.refresh();
    } catch (e) {
      setPhase("error");
      setStatusLine(null);
      setErrorLine(txErrorMessage(e));
    }
  }

  return (
    <div className="game-panel" id={panelId}>
      <div className="game-panel-core">
        <div className="game-panel-head">
          <span className="mono dim">
            challenge epoch #{epochIndex} - strategy {strategyId}
          </span>
          <button type="button" className="copy-chip" onClick={onClose} aria-label="Close challenge panel">
            close
          </button>
        </div>
        {configError !== null && (
          <p className="status-line neg" role="status">
            contract read failed: {configError}
          </p>
        )}
        {address === null ? (
          <p className="status-line warn" role="status">
            connect a wallet first: challenging requires staking USDG from your
            own balance.
          </p>
        ) : (
          <>
            <div className="desk-facts">
              <span className="kv">
                <span>challenge stake</span>
                <span>{stake === null ? "reading…" : `${formatUsdg(stake.toString())} USDG`}</span>
              </span>
              <span className="kv">
                <span>your USDG balance</span>
                <span>
                  {readError !== null ? (
                    <span className="neg">read failed</span>
                  ) : balance === null ? (
                    "reading…"
                  ) : (
                    `${formatUsdg(balance.toString())} USDG`
                  )}
                </span>
              </span>
              <span className="kv">
                <span>your allowance to the contract</span>
                <span>
                  {allowance === null
                    ? "reading…"
                    : `${formatUsdg(allowance.toString())} USDG${
                        needsApprove ? " (approve needed)" : ""
                      }`}
                </span>
              </span>
            </div>
            <div className="field game-field">
              <label className="field-label" htmlFor={`reason-${strategyId}-${epochIndex}`}>
                Reason (public, filed onchain)
              </label>
              <input
                id={`reason-${strategyId}-${epochIndex}`}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="e.g. equity print does not match the venue statement"
                maxLength={280}
                disabled={busy || phase === "done"}
              />
            </div>
            <div className="game-actions">
              {phase !== "done" && (
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={!writable || !reasonOk || busy || readError !== null}
                  onClick={() => void runChallenge()}
                >
                  {needsApprove ? "Approve stake, then challenge" : "File challenge"}
                </button>
              )}
              {phase === "done" && (
                <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
                  Done
                </button>
              )}
              {paused && <span className="badge warn">paused: writes disabled</span>}
              {!chainOk && address !== null && <span className="badge warn">wrong network</span>}
            </div>
            {statusLine !== null && (
              <p className="status-line mono" role="status" aria-live="polite">
                {statusLine}
              </p>
            )}
            {errorLine !== null && (
              <p className="status-line neg mono" role="status" aria-live="assertive">
                {errorLine}
              </p>
            )}
            <p className="honest-line">
              Economics, honestly: the stake is escrowed when you file. If the
              resolver dismisses, it is forfeited to the treasury; if it
              upholds, the epoch is invalidated and the operator&apos;s bond is
              slashed to you.
            </p>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * Resolver-only controls on a challenged epoch: Uphold (invalidate, slash the
 * operator bond) and Dismiss (forfeit the challenger stake), each behind an
 * explicit confirm click.
 */
function ResolverActions({
  strategyId,
  epochIndex,
  challenger,
  stake,
}: {
  strategyId: string;
  epochIndex: string;
  challenger: string | null;
  stake: string | null;
}) {
  const router = useRouter();
  const { contract, config, publicClient, wallet, address, chainOk, refreshConfig } = useChallenge();
  const [armed, setArmed] = useState<"uphold" | "dismiss" | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [errorLine, setErrorLine] = useState<string | null>(null);

  const isResolver =
    config !== null && address !== null && address.toLowerCase() === config.resolver.toLowerCase();
  const paused = config?.paused ?? false;
  const writable =
    contract !== null && wallet !== null && address !== null && chainOk && config !== null && !paused;
  const busy = phase === "challenging" || phase === "confirming";

  useEffect(() => {
    if (!isResolver) setArmed(null);
  }, [isResolver]);

  async function runResolve(upheld: boolean) {
    if (contract === null || wallet === null || address === null || publicClient === null) return;
    setErrorLine(null);
    try {
      setPhase("challenging");
      const hash = await wallet.writeContract({
        address: contract,
        abi: covenantAbi,
        functionName: "resolveChallenge",
        args: [BigInt(strategyId), BigInt(epochIndex), upheld],
        account: address,
        chain: wallet.chain,
      });
      setPhase("confirming");
      await publicClient.waitForTransactionReceipt({ hash });
      setPhase("done");
      setArmed(null);
      refreshConfig();
      router.refresh();
    } catch (e) {
      setPhase("error");
      setArmed(null);
      setErrorLine(txErrorMessage(e));
    }
  }

  if (!isResolver) {
    return (
      <span className="dim" title={challenger ?? undefined}>
        awaiting resolver{stake !== null && stake !== "0" ? ` - ${formatUsdg(stake)} staked` : ""}
      </span>
    );
  }

  return (
    <span className="resolve-actions">
      {armed === null ? (
        <>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={!writable || busy || phase === "done"}
            onClick={() => setArmed("uphold")}
            aria-label={`Uphold challenge of epoch ${epochIndex}: invalidate the epoch and slash the operator bond`}
          >
            Uphold
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={!writable || busy || phase === "done"}
            onClick={() => setArmed("dismiss")}
            aria-label={`Dismiss challenge of epoch ${epochIndex}: forfeit the challenger stake`}
          >
            Dismiss
          </button>
        </>
      ) : (
        <>
          <button
            type="button"
            className={`btn btn-sm ${armed === "uphold" ? "btn-primary" : "btn-secondary"}`}
            disabled={busy}
            onClick={() => void runResolve(armed === "uphold")}
          >
            Confirm {armed === "uphold" ? "uphold" : "dismiss"}
          </button>
          <button
            type="button"
            className="copy-chip"
            onClick={() => setArmed(null)}
            aria-label="Cancel resolve"
          >
            cancel
          </button>
          <span className="status-line warn">
            {armed === "uphold"
              ? "upholding invalidates the epoch and slashes the operator bond"
              : "dismissing forfeits the challenger stake"}
          </span>
        </>
      )}
      {phase === "confirming" && <span className="status-line mono">waiting for confirmation…</span>}
      {phase === "done" && <span className="status-line pos">resolved</span>}
      {errorLine !== null && (
        <span className="status-line neg mono" role="status">
          {errorLine}
        </span>
      )}
      {paused && <span className="badge warn">paused</span>}
      {!chainOk && <span className="badge warn">wrong network</span>}
    </span>
  );
}
