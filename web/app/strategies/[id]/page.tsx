import Link from "next/link";
import type { CSSProperties } from "react";
import CopyButton from "../../../components/copy-button";
import EquityChart, { type ChartPoint } from "../../../components/equity-chart";
import {
  checkpointPill,
  fetchStrategy,
  formatReturnWad,
  formatUsdg,
  statusPill,
  type EpochRow,
  type StrategyDetail,
} from "../../../lib/api";

export const dynamic = "force-dynamic";

/** "2h ago" style relative time; the absolute timestamp rides on the title. */
function relTime(iso: string | null): string {
  if (iso === null) return "-";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  if (m < 48 * 60) return `${Math.floor(m / 60)}h ago`;
  return `${Math.floor(m / 1440)}d ago`;
}

function PnlCell({ pnl }: { pnl: string | null }) {
  if (pnl === null) return <span className="dim">-</span>;
  const value = Number(pnl);
  if (value === 0) return <span className="dim">{formatUsdg(pnl)}</span>;
  const negative = pnl.startsWith("-");
  const signed = `${negative ? "" : "+"}${formatUsdg(pnl)}`;
  return <span className={negative ? "neg" : "pos"}>{signed}</span>;
}

function epochRowClass(status: number): string {
  if (status === 0 || status === 2) return "row-warn"; // pending / challenged
  if (status >= 3) return "row-bad"; // invalidated
  return "";
}

function timelineSegClass(status: number): string {
  if (status === 1) return "s-fin";
  if (status === 2) return "s-chal";
  if (status >= 3) return "s-bad";
  return "s-pending";
}

/** Stagger index for the row fade-rise animation, capped at 8 rows. */
function rowStyle(i: number): CSSProperties {
  return { "--row-i": Math.min(i, 8) } as CSSProperties;
}

function revealStyle(i: number): CSSProperties {
  return { "--reveal-i": i } as CSSProperties;
}

interface EquityDeltas {
  usd: number;
  pct: number;
  positive: boolean;
}

/** Change from the first to the last finalized checkpoint; null below 2 points. */
function equityDeltas(epochs: EpochRow[]): EquityDeltas | null {
  const fin = epochs.filter((e) => e.status === 1 && e.equity !== null);
  if (fin.length < 2) return null;
  const first = Number(fin[0].equity);
  const last = Number(fin[fin.length - 1].equity);
  if (first === 0) return null;
  const usd = last - first;
  return { usd, pct: (usd / Math.abs(first)) * 100, positive: usd >= 0 };
}

export default async function StrategyPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let detail: StrategyDetail;
  try {
    detail = await fetchStrategy(id);
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return (
      <>
        <div className="page-head">
          <p className="eyebrow">Strategy #{id}</p>
          <h1 className="display-hero">Not indexed</h1>
        </div>
        <div className="error-panel">
          <div className="panel-core">
            <div className="panel-head">
              <span>Error</span>
              <span className="panel-meta">strategy {id}</span>
            </div>
            <div className="panel-body">
              NOT INDEXED ({message})
              <br />
              Fix: run <code>RPC_URL=… CONTRACT_ADDRESS=… pnpm start</code> in{" "}
              <code>indexer/</code>.
            </div>
          </div>
        </div>
      </>
    );
  }

  const sp = statusPill(detail.status);
  const retWad = detail.derived.returnWad;
  const ret =
    retWad === null ? null : retWad === "0" ? "0.00%" : formatReturnWad(retWad);
  const retCls =
    retWad === null || retWad === "0"
      ? "dim"
      : retWad.startsWith("-")
        ? "neg"
        : "pos";
  const d = detail.derived;

  // chart input: every committed checkpoint with an equity; the chart itself
  // draws the line from finalized points only
  const chartPoints: ChartPoint[] = detail.epochs
    .filter((e) => e.equity !== null)
    .map((e) => ({
      epoch: Number(e.epochIndex),
      equity: Number(e.equity),
      pnl: e.pnl === null ? null : Number(e.pnl),
      status: e.status,
      time: e.committedAt,
    }));
  const finalizedCount = chartPoints.filter((p) => p.status === 1).length;
  const deltas = equityDeltas(detail.epochs);

  return (
    <>
      <div className="page-head reveal" style={revealStyle(0)}>
        <p className="eyebrow eyebrow-live">Strategy #{detail.id}</p>
        <div className="display-row">
          <h1 className="display-hero">
            {detail.name || `strategy-${detail.id}`}
          </h1>
          <span className={`badge ${sp.cls}`}>{sp.label}</span>
        </div>
        <p className="lede mono">
          owner {detail.owner} - bond {formatUsdg(detail.bond)} USDG (slashed to
          a successful challenger on covenant misreporting)
        </p>
      </div>

      <section className="stat-row reveal" style={revealStyle(1)} aria-label="Strategy stats">
        <div className="stat">
          <div className="stat-label">Equity (USDG)</div>
          <div className="stat-value">{formatUsdg(d.equity)}</div>
          <div className={`stat-delta ${deltas ? (deltas.positive ? "pos" : "neg") : ""}`}>
            {deltas !== null && (
              <span className="tri" aria-hidden="true">
                {deltas.positive ? "▲" : "▼"}
              </span>
            )}
            <span>
              {deltas !== null
                ? `${deltas.positive ? "+" : "-"}${formatUsdg(
                    String(Math.abs(Math.round(deltas.usd))),
                  )} (${deltas.positive ? "+" : ""}${deltas.pct.toFixed(1)}%) since first checkpoint`
                : "waiting for a second finalized checkpoint"}
            </span>
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Cumulative PnL (USDG)</div>
          <div className="stat-value">
            {d.cumulativePnl.startsWith("-") || Number(d.cumulativePnl) === 0 ? (
              formatUsdg(d.cumulativePnl)
            ) : (
              <span className="pos">+{formatUsdg(d.cumulativePnl)}</span>
            )}
          </div>
          <div className="stat-delta">
            <span>sum of {d.finalizedEpochs} finalized epoch pnl</span>
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Return</div>
          <div className={`stat-value ${retCls}`}>{ret ?? "no history"}</div>
          <div className="stat-delta">
            <span>cumulative pnl over first finalized equity</span>
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Epochs finalized</div>
          <div className="stat-value">
            {d.finalizedEpochs}
            <span className="dim"> / {detail.totalEpochs}</span>
          </div>
          <div className="stat-delta">
            <span className="warn">
              {d.pendingEpochs} pending - {d.challengedEpochs} challenged
            </span>
            {d.invalidatedEpochs > 0 && (
              <span className="neg"> - {d.invalidatedEpochs} invalidated</span>
            )}
          </div>
        </div>
      </section>

      {finalizedCount >= 2 ? (
        <figure
          className="panel chart-panel reveal"
          style={revealStyle(2)}
        >
          <div className="panel-core">
            <div className="panel-head">
              <span>Equity curve</span>
              <span className="panel-meta">
                {finalizedCount} finalized checkpoints
              </span>
            </div>
            <EquityChart points={chartPoints} />
          </div>
        </figure>
      ) : (
        <div className="empty-state">
          <div className="panel-core">
            <div className="panel-head">
              <span>Status</span>
              <span className="panel-meta">equity curve</span>
            </div>
            <div className="panel-body">
              NO EQUITY CURVE YET
              <br />
              A curve is drawn from at least 2 finalized checkpoints; this
              strategy has {finalizedCount}.
            </div>
          </div>
        </div>
      )}

      {detail.epochs.length > 0 && (
        <div className="timeline-wrap reveal" style={revealStyle(3)}>
          <div
            className="timeline"
            role="img"
            aria-label={`Epoch status timeline: ${d.finalizedEpochs} finalized, ${d.pendingEpochs} pending, ${d.challengedEpochs} challenged, ${d.invalidatedEpochs} invalidated`}
          >
            {detail.epochs.map((e) => (
              <span
                key={e.epochIndex}
                className={`timeline-seg ${timelineSegClass(e.status)}`}
                title={`epoch ${e.epochIndex}: ${checkpointPill(e.status).label}`}
              />
            ))}
          </div>
          <div className="legend">
            <span style={{ "--dot": "var(--pos)" } as CSSProperties}>
              finalized
            </span>
            <span
              style={
                {
                  "--dot": "color-mix(in srgb, var(--warn) 40%, transparent)",
                } as CSSProperties
              }
            >
              pending
            </span>
            <span style={{ "--dot": "var(--warn)" } as CSSProperties}>
              challenged
            </span>
            <span style={{ "--dot": "var(--neg)" } as CSSProperties}>
              invalidated
            </span>
          </div>
        </div>
      )}

      <h2>Epochs (finalized checkpoints only are included in returns)</h2>
      {detail.epochs.length === 0 ? (
        <div className="empty-state">
          <div className="panel-core">
            <div className="panel-head">
              <span>Status</span>
              <span className="panel-meta">no checkpoints</span>
            </div>
            <div className="panel-body">
              NO EPOCHS COMMITTED YET
              <br />
              This strategy has not committed its first equity checkpoint.
            </div>
          </div>
        </div>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Epoch</th>
                <th>Committed</th>
                <th className="num">Equity (USDG)</th>
                <th className="num">Net flow (USDG)</th>
                <th className="num">PnL (USDG)</th>
                <th>Status</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {detail.epochs.map((e: EpochRow, i) => {
                const pill = checkpointPill(e.status);
                return (
                  <tr
                    key={e.epochIndex}
                    className={epochRowClass(e.status)}
                    style={rowStyle(i)}
                  >
                    <td className="mono" data-label="Epoch">
                      #{e.epochIndex}
                    </td>
                    <td
                      className="mono dim"
                      data-label="Committed"
                      title={e.committedAt ?? undefined}
                    >
                      {relTime(e.committedAt)}
                    </td>
                    <td className="num" data-label="Equity (USDG)">
                      {formatUsdg(e.equity)}
                    </td>
                    <td className="num" data-label="Net flow (USDG)">
                      {formatUsdg(e.netFlow)}
                    </td>
                    <td className="num" data-label="PnL (USDG)">
                      <PnlCell pnl={e.pnl} />
                    </td>
                    <td data-label="Status">
                      <span className={`badge ${pill.cls}`} style={rowStyle(i)}>
                        {pill.label}
                      </span>
                    </td>
                    <td data-label="Evidence">
                      {e.evidenceUri === null ? (
                        <span className="dim">-</span>
                      ) : (
                        <span className="evidence-chip">
                          <span className="evidence-uri" title={e.evidenceUri}>
                            {e.evidenceUri}
                          </span>
                          <CopyButton value={e.evidenceUri} />
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="panel cta-panel reveal" style={revealStyle(4)}>
        <div className="panel-core">
          <div className="cta-inner">
            <div>
              <h2 className="cta-title">Audit a fill</h2>
              <p className="cta-copy">
                Any disclosed trade receipt can be checked against the committed
                epoch root. Take the receipt hash and Merkle proof from the
                strategy&apos;s evidence bundle, then verify them against the
                chain straight from your browser.
              </p>
            </div>
            <Link className="btn btn-secondary" href="/verify">
              Verify a receipt
              <span className="btn-arrow" aria-hidden="true">
                &rarr;
              </span>
            </Link>
          </div>
        </div>
      </div>
    </>
  );
}
