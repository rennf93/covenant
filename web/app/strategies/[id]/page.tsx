import Link from "next/link";
import type { CSSProperties } from "react";
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

/** Stagger index for the row fade-rise animation, capped at 8 rows. */
function rowStyle(i: number): CSSProperties {
  return { "--row-i": Math.min(i, 8) } as CSSProperties;
}

/**
 * Equity sparkline as a pure server-rendered SVG polyline: no chart library,
 * no client JS. Points come from finalized epoch checkpoints only, min-max
 * normalized with label padding. Renders nothing below 2 points, so there is
 * never a fake chart.
 */
function EquitySparkline({ epochs }: { epochs: EpochRow[] }) {
  const points = epochs
    .filter((e) => e.status === 1 && e.equity !== null)
    .map((e) => ({ epoch: e.epochIndex, value: Number(e.equity), label: e.equity as string }));
  if (points.length < 2) return null;

  const W = 720;
  const H = 180;
  const TOP = 20;
  const BOT = H - 20;
  const PADX = 6;

  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;
  const y = (v: number) =>
    range === 0 ? (TOP + BOT) / 2 : TOP + (1 - (v - min) / range) * (BOT - TOP);
  const x = (i: number) => PADX + (i * (W - 2 * PADX)) / (points.length - 1);

  const polyline = points
    .map((p, i) => `${x(i).toFixed(2)},${y(p.value).toFixed(2)}`)
    .join(" ");
  const up = points[points.length - 1].value >= points[0].value;
  const stroke = up ? "var(--pos)" : "var(--neg)";
  const midY = (TOP + BOT) / 2;
  const xLast = x(points.length - 1);
  const yLast = y(points[points.length - 1].value);
  // keep the last-value label off the top gridline when the curve ends at max
  const lastLabelY = yLast < TOP + 26 ? yLast + 22 : yLast - 10;

  return (
    <figure className="spark-panel">
      <div className="panel-head">
        <span>Equity curve</span>
        <span className="panel-meta">
          {points.length} finalized checkpoints
        </span>
      </div>
      <div className="spark-body">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label="Equity across finalized epoch checkpoints"
        >
          <line x1={PADX} y1={TOP} x2={W - PADX} y2={TOP} className="spark-grid" />
          <line x1={PADX} y1={midY} x2={W - PADX} y2={midY} className="spark-grid" />
          <line x1={PADX} y1={BOT} x2={W - PADX} y2={BOT} className="spark-grid" />
          <text x={PADX} y={TOP - 7} className="spark-label">
            {formatUsdg(String(max))}
          </text>
          <text x={PADX} y={H - 6} className="spark-label">
            {formatUsdg(String(min))}
          </text>
          <text x={xLast} y={lastLabelY} textAnchor="end" className="spark-label">
            {formatUsdg(String(points[points.length - 1].value))}
          </text>
          <polyline
            points={polyline}
            className="spark-line"
            style={{ stroke }}
            vectorEffect="non-scaling-stroke"
          />
          <circle cx={xLast} cy={yLast} r="3" style={{ fill: stroke }} />
          {points.map((p, i) => (
            <circle
              key={p.epoch}
              cx={x(i)}
              cy={y(p.value)}
              r="10"
              className="spark-hit"
            >
              <title>{`epoch ${p.epoch}: ${formatUsdg(p.label)}`}</title>
            </circle>
          ))}
        </svg>
      </div>
    </figure>
  );
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

  return (
    <>
      <div className="page-head">
        <p className="eyebrow">Strategy #{detail.id}</p>
        <div className="display-row">
          <h1 className="display-hero">
            {detail.name || `strategy-${detail.id}`}
          </h1>
          <span className={`badge ${sp.cls}`}>{sp.label}</span>
        </div>
        <p className="lede mono">
          owner {detail.owner} - bond {formatUsdg(detail.bond)} USDG (slashed to
          a successful challenger on proven misreporting)
        </p>
      </div>

      <div className="stat-row">
        <div className="stat">
          <div className="stat-label">Equity (USDG)</div>
          <div className="stat-value">{formatUsdg(d.equity)}</div>
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
        </div>
        <div className="stat">
          <div className="stat-label">Return</div>
          <div className={`stat-value ${retCls}`}>{ret ?? "no history"}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Epochs finalized</div>
          <div className="stat-value">
            {d.finalizedEpochs}
            <span className="dim"> / {detail.totalEpochs}</span>
          </div>
        </div>
      </div>

      <EquitySparkline epochs={detail.epochs} />

      <p className="table-foot">
        pending {d.pendingEpochs} - challenged {d.challengedEpochs} -
        invalidated {d.invalidatedEpochs}
      </p>

      <h2>Epochs (finalized checkpoints only are included in returns)</h2>
      {detail.epochs.length === 0 ? (
        <div className="empty-state">
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
                      <span className={`badge ${pill.cls}`}>{pill.label}</span>
                    </td>
                    <td data-label="Evidence">
                      {e.evidenceUri === null ? (
                        <span className="dim">-</span>
                      ) : (
                        <span
                          className="mono dim truncate"
                          title={e.evidenceUri}
                        >
                          {e.evidenceUri}
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

      <h2>Audit a fill</h2>
      <p className="lede">
        Any disclosed trade receipt can be checked against the committed epoch
        root. Take the receipt hash and Merkle proof from the strategy&apos;s
        evidence bundle, then paste them on the{" "}
        <Link href="/verify" className="text-link">
          verification page
        </Link>
        .
      </p>
    </>
  );
}
