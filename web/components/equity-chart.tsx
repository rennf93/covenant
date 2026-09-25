"use client";

import { useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";

/**
 * Full-width equity chart, zero dependencies: one SVG area chart drawn from
 * committed epoch checkpoints. Finalized epochs form the line; pending and
 * challenged epochs render as hollow amber markers and invalidated as red,
 * never connected into the line. Pointer and keyboard both drive a crosshair
 * tooltip; arrow keys move the active point when the svg is focused.
 */

export interface ChartPoint {
  epoch: number;
  equity: number;
  pnl: number | null;
  /** 0 pending, 1 finalized, 2 challenged, 3+ invalidated. */
  status: number;
  time: string | null;
}

const W = 1000;
const H = 300;
const PADL = 10;
const PADR = 10;
const PADT = 26;
const PADB = 26;
/** One chart per page, so the svg gradient can use a fixed id. */
const GRAD_ID = "ec-area-gradient";

function statusLabel(status: number): string {
  if (status === 0) return "pending";
  if (status === 1) return "finalized";
  if (status === 2) return "challenged";
  return "invalidated";
}

/** Formats a base-unit (1e6) USDG number for display; the chart's y math is
    scale-relative, so only this presentation helper needs the conversion. */
function usd(baseUnits: number): string {
  return (baseUnits / 1e6).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export default function EquityChart({ points }: { points: ChartPoint[] }) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [keyIdx, setKeyIdx] = useState<number | null>(null);

  const finalized = useMemo(() => {
    const fin = points
      .map((p, i) => ({ p, i }))
      .filter(({ p }) => p.status === 1);
    return fin;
  }, [points]);

  // Never draw a chart from fewer than 2 finalized checkpoints.
  if (finalized.length < 2 || points.length < 2) return null;

  const minEp = points[0].epoch;
  const maxEp = points[points.length - 1].epoch;
  const epSpan = Math.max(1, maxEp - minEp);
  const x = (epoch: number) => PADL + ((epoch - minEp) / epSpan) * (W - PADL - PADR);

  const equities = points.map((p) => p.equity);
  const vMin = Math.min(...equities);
  const vMax = Math.max(...equities);
  const vSpan = vMax - vMin;
  const y = (v: number) =>
    vSpan === 0
      ? (PADT + (H - PADB)) / 2
      : PADT + (1 - (v - vMin) / vSpan) * (H - PADT - PADB);

  const linePts = finalized.map(({ p }) => `${x(p.epoch).toFixed(2)},${y(p.equity).toFixed(2)}`);
  const linePath = `M${linePts.join(" L")}`;
  const areaPath = `${linePath} L${x(finalized[finalized.length - 1].p.epoch).toFixed(2)},${H - PADB} L${x(finalized[0].p.epoch).toFixed(2)},${H - PADB} Z`;
  const up =
    finalized[finalized.length - 1].p.equity >= finalized[0].p.equity;
  const stroke = up ? "var(--pos)" : "var(--neg)";
  const lastFin = finalized[finalized.length - 1];

  const activeIdx = hoverIdx ?? keyIdx;
  const active = activeIdx === null ? null : points[activeIdx];

  function nearest(clientX: number): number | null {
    const svg = svgRef.current;
    if (svg === null) return null;
    const rect = svg.getBoundingClientRect();
    const relX = ((clientX - rect.left) / rect.width) * W;
    let best: number | null = null;
    let bestDist = Infinity;
    for (let i = 0; i < points.length; i++) {
      const d = Math.abs(x(points[i].epoch) - relX);
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    }
    return best;
  }

  function onPointerMove(e: PointerEvent<SVGSVGElement>) {
    setHoverIdx(nearest(e.clientX));
  }

  function onKeyDown(e: KeyboardEvent<SVGSVGElement>) {
    const cur = keyIdx ?? hoverIdx ?? finalized[0].i;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const step = e.key === "ArrowRight" ? 1 : -1;
      const next = Math.min(points.length - 1, Math.max(0, cur + step));
      setKeyIdx(next);
    } else if (e.key === "Home") {
      e.preventDefault();
      setKeyIdx(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setKeyIdx(points.length - 1);
    } else if (e.key === "Escape") {
      setKeyIdx(null);
      setHoverIdx(null);
    }
  }

  const gradRef = `url(#${GRAD_ID})`;
  const tooltipLeft = active === null ? 0 : (x(active.epoch) / W) * 100;
  const flip = active !== null && x(active.epoch) > W * 0.62;

  return (
    <div className="chart-body">
      <svg
        ref={svgRef}
        className="chart-svg"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Equity chart across ${finalized.length} finalized epoch checkpoints, ${points.length} epochs committed`}
        tabIndex={0}
        onPointerMove={onPointerMove}
        onPointerLeave={() => setHoverIdx(null)}
        onKeyDown={onKeyDown}
        onBlur={() => setKeyIdx(null)}
      >
        <defs>
          <linearGradient id={GRAD_ID} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* hairline gridlines with min/max labels */}
        <line x1={PADL} y1={PADT} x2={W - PADR} y2={PADT} className="ec-grid" />
        <line x1={PADL} y1={(PADT + H - PADB) / 2} x2={W - PADR} y2={(PADT + H - PADB) / 2} className="ec-grid" />
        <line x1={PADL} y1={H - PADB} x2={W - PADR} y2={H - PADB} className="ec-grid" />
        <text x={PADL} y={PADT - 9} className="ec-label">
          {usd(vMax)}
        </text>
        <text x={PADL} y={H - 8} className="ec-label">
          {usd(vMin)}
        </text>

        <path d={areaPath} fill={gradRef} />
        <path d={linePath} className="ec-line" style={{ stroke }} vectorEffect="non-scaling-stroke" />

        {/* unverified epochs: hollow markers, deliberately off the line */}
        {points.map((p, i) =>
          p.status === 1 ? null : (
            <circle
              key={p.epoch}
              cx={x(p.epoch)}
              cy={y(p.equity)}
              r="4.5"
              className={`ec-point ${p.status >= 3 ? "ec-point-bad" : "ec-point-warn"}`}
            >
              <title>{`epoch ${p.epoch}: ${usd(p.equity)} (${statusLabel(p.status)})`}</title>
            </circle>
          ),
        )}

        {/* pulsing end point on the last finalized checkpoint */}
        <circle cx={x(lastFin.p.epoch)} cy={y(lastFin.p.equity)} r="3.4" fill={stroke} />
        <circle cx={x(lastFin.p.epoch)} cy={y(lastFin.p.equity)} r="3.4" fill={stroke} className="ec-end" />

        {/* crosshair + active ring */}
        {active !== null && (
          <>
            <line
              x1={x(active.epoch)}
              y1={PADT}
              x2={x(active.epoch)}
              y2={H - PADB}
              className="ec-crosshair"
            />
            <circle
              cx={x(active.epoch)}
              cy={y(active.equity)}
              r="5.5"
              className="ec-active"
            />
          </>
        )}
      </svg>

      {active !== null && (
        <div
          className="ec-tip"
          style={{ left: `${tooltipLeft}%`, top: 0, transform: flip ? "translateX(calc(-100% - 14px))" : "translateX(14px)" } as CSSProperties}
        >
          <div className="ec-tip-title">epoch {active.epoch}</div>
          <div className="ec-tip-row">
            <span>equity</span>
            <span>{usd(active.equity)}</span>
          </div>
          <div className="ec-tip-row">
            <span>pnl</span>
            <span className={active.pnl === null ? "dim" : active.pnl < 0 ? "neg" : "pos"}>
              {active.pnl === null ? "-" : `${active.pnl >= 0 ? "+" : ""}${usd(active.pnl)}`}
            </span>
          </div>
          <div className="ec-tip-row">
            <span>status</span>
            <span
              className={
                active.status === 1
                  ? "pos"
                  : active.status >= 3
                    ? "neg"
                    : active.status === 2
                      ? "warn"
                      : "warn"
              }
            >
              {statusLabel(active.status)}
            </span>
          </div>
          {active.time !== null && (
            <div className="ec-tip-row">
              <span>committed</span>
              <span>{active.time.slice(0, 16).replace("T", " ")}</span>
            </div>
          )}
        </div>
      )}

      {/* keyboard users get an equivalent summary of the active point */}
      <p className="visually-hidden" aria-live="polite">
        {active === null
          ? "Use arrow keys to inspect epochs."
          : `Epoch ${active.epoch}: equity ${usd(active.equity)} USDG, ${statusLabel(active.status)}${active.pnl === null ? "" : `, pnl ${usd(active.pnl)} USDG`}.`}
      </p>
    </div>
  );
}
