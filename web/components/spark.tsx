import type { CSSProperties } from "react";

/**
 * Inline SVG sparklines, no libraries: gradient area fill, glow line, pulsing
 * end dot (pure CSS via .spark-end). Server-safe; color rides on `color` so
 * currentColor glow stays in CSS. Never renders from fewer than 2 points:
 * callers must pass real finalized data.
 */

/** Document-level gradient defs so every spark can share them by url(#id). */
export function SparkDefs() {
  return (
    <svg
      width="0"
      height="0"
      aria-hidden="true"
      focusable="false"
      style={{ position: "absolute" } as CSSProperties}
    >
      <defs>
        <linearGradient id="spark-fill-pos" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#45e8a0" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#45e8a0" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="spark-fill-neg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ff6b64" stopOpacity="0.18" />
          <stop offset="100%" stopColor="#ff6b64" stopOpacity="0" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export function Sparkline({
  points,
  width,
  height,
  className,
  ariaLabel,
}: {
  points: number[];
  width: number;
  height: number;
  className?: string;
  /** When omitted the svg is marked aria-hidden (decorative in a data table). */
  ariaLabel?: string;
}) {
  if (points.length < 2) return null;
  const PAD = 3;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min;
  const x = (i: number) => PAD + (i * (width - 2 * PAD)) / (points.length - 1);
  const y = (v: number) =>
    range === 0 ? height / 2 : PAD + (1 - (v - min) / range) * (height - 2 * PAD);
  const line = points.map((v, i) => `${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(" ");
  const up = points[points.length - 1] >= points[0];
  const gradId = up ? "spark-fill-pos" : "spark-fill-neg";
  const xLast = Number(x(points.length - 1).toFixed(2));
  const yLast = Number(y(points[points.length - 1]).toFixed(2));
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={className}
      style={{ color: up ? "var(--pos)" : "var(--neg)" }}
      role={ariaLabel === undefined ? undefined : "img"}
      aria-label={ariaLabel}
      aria-hidden={ariaLabel === undefined || undefined}
    >
      <polygon
        points={`${line} ${xLast},${height - PAD} ${x(0).toFixed(2)},${height - PAD}`}
        fill={`url(#${gradId})`}
      />
      <polyline
        points={line}
        className="spark-line"
        style={{ stroke: "currentColor" }}
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={xLast} cy={yLast} r="2.4" fill="currentColor" />
      <circle cx={xLast} cy={yLast} r="2.4" fill="currentColor" className="spark-end" />
    </svg>
  );
}
