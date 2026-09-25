import Link from "next/link";
import type { CSSProperties } from "react";
import {
  fetchDemoFlag,
  fetchLeaderboard,
  formatReturnWad,
  formatUsdg,
  statusPill,
  type LeaderboardRow,
} from "../lib/api";

export const dynamic = "force-dynamic";

/** Stagger index for the row fade-rise animation, capped so long lists do not lag. */
function rowStyle(i: number): CSSProperties {
  return { "--row-i": Math.min(i, 8) } as CSSProperties;
}

/**
 * Protocol pulse: whole-protocol aggregates computed from the same rows the
 * table renders. No extra fetches, no fabrication.
 */
function buildPulseStats(rows: LeaderboardRow[]) {
  const totalEpochs = rows.reduce((acc, r) => acc + r.totalEpochs, 0);
  const finalized = rows.reduce((acc, r) => acc + r.finalizedEpochs, 0);
  const bonded = rows.reduce((acc, r) => acc + Number(r.bond), 0);
  return {
    strategies: rows.length.toLocaleString("en-US"),
    epochs: totalEpochs.toLocaleString("en-US"),
    bonded: bonded.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }),
    finalizedPct:
      totalEpochs === 0
        ? "0"
        : Math.round((finalized / totalEpochs) * 100).toLocaleString("en-US"),
  };
}

function Rank({ i }: { i: number }) {
  return <span className="mono dim">#{i + 1}</span>;
}

function ReturnCell({ row }: { row: LeaderboardRow }) {
  const wad = row.derived.returnWad;
  if (wad === null) return <span className="dim">no history</span>;
  if (wad === "0") return <span className="dim">0.00%</span>;
  const positive = !wad.startsWith("-");
  return (
    <span className={positive ? "pos" : "neg"}>
      {formatReturnWad(wad)}
    </span>
  );
}

function EpochsCell({ row }: { row: LeaderboardRow }) {
  return (
    <span className="mono">
      {row.finalizedEpochs} fin / {row.totalEpochs} total
      {row.derived.pendingEpochs > 0 && (
        <span className="warn"> +{row.derived.pendingEpochs} pending</span>
      )}
    </span>
  );
}

export default async function LeaderboardPage() {
  let rows: LeaderboardRow[] | null = null;
  let error: string | null = null;
  let demo = false;
  try {
    rows = await fetchLeaderboard();
    demo = await fetchDemoFlag();
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const pulse = rows !== null && rows.length > 0 ? buildPulseStats(rows) : null;

  return (
    <>
      {demo && (
        <div className="banner-warn">
          <div className="panel-head">
            <span>Status</span>
            <span className="panel-meta">synthetic indexer</span>
          </div>
          <div className="panel-body">
            DEMO DATA - synthetic indexer, not live onchain. This is a UI
            preview before contract deployment; every number here is fabricated.
          </div>
        </div>
      )}

      <div className="page-head">
        <p className="eyebrow">Onchain proof of performance</p>
        <h1 className="display">Proof, not promises.</h1>
        <p className="lede">
          Every strategy commits daily equity checkpoints and a Merkle root of
          its trade receipts to Arbitrum. Returns shown are computed from
          finalized checkpoints only. Pending epochs are unverified until the
          challenge window closes.
        </p>
      </div>

      {error !== null && (
        <div className="error-panel">
          <div className="panel-head">
            <span>Error</span>
            <span className="panel-meta">indexer unreachable</span>
          </div>
          <div className="panel-body">
            INDEXER UNREACHABLE ({error})
            <br />
            Fix: run{" "}
            <code>RPC_URL=… CONTRACT_ADDRESS=… pnpm start</code> in{" "}
            <code>indexer/</code>.
          </div>
        </div>
      )}

      {rows !== null && rows.length === 0 && (
        <div className="empty-state">
          <div className="panel-head">
            <span>Status</span>
            <span className="panel-meta">0 registered</span>
          </div>
          <div className="panel-body">
            NO STRATEGIES REGISTERED
            <br />
            Be the first: register with a USDG bond and start committing epochs.
          </div>
        </div>
      )}

      {pulse !== null && (
        <section className="pulse-band" aria-label="Protocol pulse">
          <div className="pulse-stat">
            <div className="stat-label">Strategies registered</div>
            <div className="pulse-value">{pulse.strategies}</div>
          </div>
          <div className="pulse-stat">
            <div className="stat-label">Epochs committed</div>
            <div className="pulse-value">{pulse.epochs}</div>
          </div>
          <div className="pulse-stat">
            <div className="stat-label">USDG bonded</div>
            <div className="pulse-value">{pulse.bonded}</div>
          </div>
          <div className="pulse-stat">
            <div className="stat-label">Finalized share</div>
            <div className="pulse-value">{pulse.finalizedPct}%</div>
          </div>
        </section>
      )}

      {rows !== null && rows.length > 0 && (
        <>
          <h2>Strategies</h2>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Strategy</th>
                  <th>Status</th>
                  <th className="num">Return</th>
                  <th className="num">Cumulative PnL (USDG)</th>
                  <th className="num">Equity (USDG)</th>
                  <th className="num">Bond (USDG)</th>
                  <th className="num">Epochs</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => {
                  const pill = statusPill(row.status);
                  return (
                    <tr key={row.id} style={rowStyle(i)}>
                      <td data-label="Rank">
                        <Rank i={i} />
                      </td>
                      <td data-label="Strategy">
                        <span className="cell-main">
                          <Link
                            href={`/strategies/${row.id}`}
                            className="row-link"
                          >
                            {row.name || `strategy-${row.id}`}
                          </Link>
                          <span className="sub">{row.owner}</span>
                        </span>
                      </td>
                      <td data-label="Status">
                        <span className={`badge ${pill.cls}`}>
                          {pill.label}
                        </span>
                      </td>
                      <td className="num" data-label="Return">
                        <ReturnCell row={row} />
                      </td>
                      <td className="num" data-label="Cumulative PnL (USDG)">
                        {formatUsdg(row.derived.cumulativePnl)}
                      </td>
                      <td className="num" data-label="Equity (USDG)">
                        {formatUsdg(row.derived.equity)}
                      </td>
                      <td className="num" data-label="Bond (USDG)">
                        {formatUsdg(row.bond)}
                      </td>
                      <td className="num" data-label="Epochs">
                        <EpochsCell row={row} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="table-foot">
            Returns are computed from finalized checkpoints only. Challenge a
            pending epoch by staking USDG.
          </p>
        </>
      )}
    </>
  );
}
