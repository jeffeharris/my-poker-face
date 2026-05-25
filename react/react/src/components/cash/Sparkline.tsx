/**
 * Sparkline — a tiny dependency-free SVG trend line.
 *
 * Renders `values` (oldest → newest) as a normalized polyline with a
 * soft gradient area fill and a glowing endpoint dot. Auto-scales to the
 * series min/max, so it reads as *shape* (is my bankroll climbing or
 * sliding?) rather than absolute magnitude — the precise figure lives in
 * the hero number above it.
 *
 * Pure presentational + reference-stable: no state, no effects. Renders
 * nothing for fewer than two points (a single session can't show a
 * trend).
 */

import { useId } from 'react';

export interface SparklineProps {
  /** Data points, oldest → newest. Needs ≥2 to draw. */
  values: number[];
  /** Trend tone — drives stroke + fill hue. `null` → neutral gold. */
  tone?: 'up' | 'down' | 'flat' | null;
  /** Intrinsic viewBox size; the SVG scales to its CSS box. */
  width?: number;
  height?: number;
  className?: string;
}

const TONE_COLORS: Record<'up' | 'down' | 'flat', string> = {
  up: '#34d399', // emerald
  down: '#f43f5e', // ruby
  flat: '#d4a574', // gold
};

export function Sparkline({
  values,
  tone = null,
  width = 240,
  height = 48,
  className = '',
}: SparklineProps) {
  const gradientId = useId();
  if (!values || values.length < 2) return null;

  const stroke = TONE_COLORS[tone ?? 'flat'];

  // Leave 2px of vertical breathing room so the stroke + dot aren't clipped.
  const pad = 3;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1; // avoid /0 when the line is dead flat
  const stepX = width / (values.length - 1);

  const points = values.map((v, i) => {
    const x = i * stepX;
    const y = pad + (1 - (v - min) / span) * (height - pad * 2);
    return [x, y] as const;
  });

  const linePath = points
    .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ');

  // Close the path down to the baseline for the area fill.
  const [lastX] = points[points.length - 1];
  const areaPath = `${linePath} L${lastX.toFixed(1)},${height} L0,${height} Z`;

  const [endX, endY] = points[points.length - 1];

  return (
    <svg
      className={`sparkline ${className}`.trim()}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Bankroll trend"
    >
      <defs>
        <linearGradient id={`spark-fill-${gradientId}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#spark-fill-${gradientId})`} stroke="none" />
      <path
        d={linePath}
        fill="none"
        stroke={stroke}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {/* Endpoint marker — the "you are here" pulse. */}
      <circle cx={endX} cy={endY} r={3.5} fill={stroke} className="sparkline__dot" />
    </svg>
  );
}
