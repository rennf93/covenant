import Link from "next/link";
import type { CSSProperties } from "react";
import { SparkDefs, Sparkline } from "../components/spark";
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
  return { "--row-i": Math.min(i, 9) } as CSSProperties;
}

/** Stagger index for section entry reveals. */
function revealStyle(i: number): CSSProperties {
  return { "--reveal-i": i } as CSSProperties;
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

/**
 * Tail-aligned index-wise sum of the rows' finalized-equity sparks. Only arrays
 * that actually exist are summed, and the tile is skipped when anything is
 * missing, so a chart here is always an honest aggregate of real rows.
 */
function combinedSpark(rows: LeaderboardRow[]): number[] | null {
  const series: (number | null)[][] = [];
  for (const r of rows) {
    if (Array.isArray(r.spark) && r.spark.length >= 2) series.push(r.spark);
  }
  if (series.length === 0) return null;
  const n = Math.min(...series.map((s) => s.length));
  if (n < 2) return null;
  const sums: number[] = [];
  for (let i = 0; i < n; i++) {
    let sum = 0;
    for (const s of series) {
      const v = s[s.length - n + i];
      if (typeof v !== "number") return null;
      sum += v;
    }
    sums.push(sum);
  }
  return sums;
}

function Rank({ i }: { i: number }) {
  return <span className="mono dim">#{i + 1}</span>;
}

function ReturnCell({ row }: { row: LeaderboardRow }) {
  const wad = row.derived.returnWad;
  if (wad === null) return <span className="dim">no history</span>;
  const pct = Number(wad) / 1e16;
  if (pct === 0) return <span className="dim">0.00%</span>;
  const positive = pct > 0;
  // centered-zero bar: 100% return fills the whole half, capped there
  const half = Math.min(50, Math.abs(pct));
  const fillStyle: CSSProperties = positive
    ? { right: "50%", width: `${half}%` }
    : { left: "50%", width: `${half}%` };
  return (
    <span className="ret-cell">
      <span className="ret-bar" aria-hidden="true">
        <span className={`ret-fill ${positive ? "rpos" : "rneg"}`} style={fillStyle} />
      </span>
      <span className={positive ? "pos" : "neg"}>{formatReturnWad(wad)}</span>
    </span>
  );
}

function TrendCell({ row }: { row: LeaderboardRow }) {
  // Array.isArray guards against an indexer predating the spark field
  const spark = Array.isArray(row.spark)
    ? row.spark.filter((v): v is number => typeof v === "number")
    : [];
  if (spark.length < 2) return <span className="dim">-</span>;
  return (
    <Sparkline
      points={spark}
      width={96}
      height={32}
      className="trend-spark"
      ariaLabel={`Equity trend across last ${spark.length} finalized epochs`}
    />
  );
}

function EpochsCell({ row }: { row: LeaderboardRow }) {
  return (
    <span className="mono">
      {row.finalizedEpochs} fin / {row.totalEpochs}
      {row.derived.pendingEpochs > 0 && (
        <span className="warn"> +{row.derived.pendingEpochs} pend</span>
      )}
    </span>
  );
}

/** Shortform of a 0x address for dense table cells; full value rides on title. */
function shortAddr(addr: string): string {
  return addr.length > 14 ? `${addr.slice(0, 8)}…${addr.slice(-4)}` : addr;
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
  const totals = rows !== null && rows.length > 0 ? combinedSpark(rows) : null;
  const totalsValue =
    totals !== null
      ? totals[totals.length - 1].toLocaleString("en-US", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })
      : null;

  return (
    <>
      <SparkDefs />

      {demo && (
        <div className="banner-warn" role="note">
          <div className="panel-core">
            <div className="panel-head">
              <span>Demo data</span>
              <span className="panel-meta">synthetic indexer</span>
            </div>
            <div className="panel-body">
              DEMO DATA - synthetic indexer, not live onchain. This is a UI
              preview before contract deployment; every number here is fabricated.
            </div>
          </div>
        </div>
      )}

      <section className="page-head reveal" style={revealStyle(0)}>
        <p className="eyebrow eyebrow-live">Onchain proof of performance</p>
        <h1 className="display">
          <span className="grad-text">Proof</span>, not promises.
        </h1>
        <p className="lede">
          Every strategy commits daily equity checkpoints and a Merkle root of
          its trade receipts to Arbitrum. Returns shown are computed from
          finalized checkpoints only. Pending epochs are unverified until the
          challenge window closes.
        </p>
        <div className="cta-row">
          <a className="btn btn-primary" href="#strategies">
            Explore strategies
            <span className="btn-arrow" aria-hidden="true">
              &rarr;
            </span>
          </a>
          <Link className="btn btn-secondary" href="/verify">
            Verify a receipt
            <span className="btn-arrow" aria-hidden="true">
              &rarr;
            </span>
          </Link>
        </div>
      </section>

      {error !== null && (
        <div className="error-panel">
          <div className="panel-core">
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
        </div>
      )}

      {rows !== null && rows.length === 0 && (
        <div className="empty-state">
          <div className="panel-core">
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
        </div>
      )}

      {pulse !== null && (
        <section
          className="panel pulse-panel reveal"
          style={revealStyle(1)}
          aria-label="Protocol pulse"
        >
          <div className="panel-core">
            <div className="panel-head">
              <span>Protocol pulse</span>
              <span className="panel-meta">derived from indexed state</span>
            </div>
            <div className="pulse-grid">
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
              {totals !== null && totalsValue !== null && (
                <div className="pulse-stat pulse-stat--wide">
                  <div>
                    <div className="stat-label">Combined equity (USDG)</div>
                    <div className="pulse-value">{totalsValue}</div>
                    <div className="stat-delta">
                      tail-aligned sum of {totals.length}-checkpoint strategy
                      trends, finalized epochs only
                    </div>
                  </div>
                  <Sparkline
                    points={totals}
                    width={320}
                    height={64}
                    className="tile-spark"
                    ariaLabel="Combined equity across strategies with finalized history"
                  />
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {rows !== null && rows.length > 0 && (
        <section
          className="ticker reveal"
          style={revealStyle(2)}
          aria-label="Attestation ticker"
        >
          <div className="ticker-track">
            {[0, 1].map((copy) => (
              <div
                className="ticker-group"
                key={copy}
                aria-hidden={copy === 1 || undefined}
              >
                {rows.map((row) => {
                  const wad = row.derived.returnWad;
                  return (
                    <span className="ticker-item" key={`${copy}-${row.id}`}>
                      <span className="ticker-name">
                        {row.name || `strategy-${row.id}`}
                      </span>
                      {wad === null ? (
                        <span className="dim">no history</span>
                      ) : (
                        <span
                          className={
                            wad.startsWith("-") || wad === "0" ? "dim" : "pos"
                          }
                        >
                          {formatReturnWad(wad)}
                        </span>
                      )}
                      <span className="dim">
                        {row.finalizedEpochs}f / {row.totalEpochs}e
                      </span>
                      <span className="dim">bond {formatUsdg(row.bond)}</span>
                    </span>
                  );
                })}
              </div>
            ))}
          </div>
        </section>
      )}

      {rows !== null && rows.length > 0 && (
        <>
          <h2 id="strategies">Strategies</h2>
          <div className="panel reveal" style={revealStyle(3)}>
            <div className="panel-core">
              <div className="panel-head">
                <span>Leaderboard</span>
                <span className="panel-meta">best return first</span>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Strategy</th>
                      <th>Status</th>
                      <th>Trend</th>
                      <th className="num">Return</th>
                      <th className="num">PnL (USDG)</th>
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
                              <span className="sub" title={row.owner}>
                                {shortAddr(row.owner)}
                              </span>
                            </span>
                          </td>
                          <td data-label="Status">
                            <span
                              className={`badge ${pill.cls}`}
                              style={rowStyle(i)}
                            >
                              {pill.label}
                            </span>
                          </td>
                          <td data-label="Trend">
                            <TrendCell row={row} />
                          </td>
                          <td className="num" data-label="Return">
                            <ReturnCell row={row} />
                          </td>
                          <td className="num" data-label="PnL (USDG)">
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
            </div>
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
