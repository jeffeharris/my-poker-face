/**
 * Sparkline — a tiny dependency-free SVG trend line with hover scrubbing.
 *
 * Renders `points` (oldest → newest `{t, value}` change-points) as a
 * normalized polyline with a soft gradient area fill and a glowing
 * endpoint dot. Auto-scales to the series min/max, so it reads as *shape*
 * (is my net worth climbing or sliding?) rather than absolute magnitude —
 * the precise figure lives in the hero number above it.
 *
 * Interactive: moving the pointer (or dragging on touch) snaps to the
 * nearest vertex and shows a tooltip with that point's amount and the
 * timestamp it was reached, plus a cursor line + marker on the curve.
 *
 * Renders nothing for fewer than two points (a single point can't show a
 * trend).
 */

import { useId, useRef, useState } from 'react';
import type { BankrollPoint } from './types';
import './Sparkline.css';

export interface SparklineProps {
  /** Change-points, oldest → newest. Needs ≥2 to draw. */
  points: BankrollPoint[];
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

const PAD = 3; // vertical breathing room so the stroke + dot aren't clipped

function formatAmount(n: number): string {
  // Unicode minus for negatives so a debt point reads cleanly.
  return `${n < 0 ? '−' : ''}$${Math.abs(n).toLocaleString()}`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function Sparkline({
  points,
  tone = null,
  width = 240,
  height = 48,
  className = '',
}: SparklineProps) {
  const gradientId = useId();
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  if (!points || points.length < 2) return null;

  const stroke = TONE_COLORS[tone ?? 'flat'];
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1; // avoid /0 when the line is dead flat
  const stepX = width / (points.length - 1);

  const coords = values.map((v, i) => {
    const x = i * stepX;
    const y = PAD + (1 - (v - min) / span) * (height - PAD * 2);
    return [x, y] as const;
  });

  const linePath = coords
    .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ');

  // Close the path down to the baseline for the area fill.
  const [lastX] = coords[coords.length - 1];
  const areaPath = `${linePath} L${lastX.toFixed(1)},${height} L0,${height} Z`;

  const [endX, endY] = coords[coords.length - 1];

  // --- hover scrubbing (mouse + touch) ---
  const lastIdx = points.length - 1;
  const pickFromClientX = (clientX: number) => {
    const el = wrapRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0) return;
    const frac = (clientX - rect.left) / rect.width;
    const idx = Math.max(0, Math.min(lastIdx, Math.round(frac * lastIdx)));
    setHover(idx);
  };

  const hovered = hover != null ? points[hover] : null;
  const hoverXPct = hover != null ? (hover / lastIdx) * 100 : 0;
  const hoverYPct = hover != null ? (coords[hover][1] / height) * 100 : 0;
  // Keep the tooltip on-screen at the ends.
  const tipAlign = hoverXPct < 18 ? 'left' : hoverXPct > 82 ? 'right' : 'center';

  return (
    <div
      ref={wrapRef}
      className={`sparkline-wrap ${className}`.trim()}
      onMouseMove={(e) => pickFromClientX(e.clientX)}
      onMouseLeave={() => setHover(null)}
      onTouchStart={(e) => pickFromClientX(e.touches[0].clientX)}
      onTouchMove={(e) => pickFromClientX(e.touches[0].clientX)}
      onTouchEnd={() => setHover(null)}
    >
      <svg
        className="sparkline"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Net worth trend"
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
        {/* Endpoint marker — the "you are here" pulse (hidden while scrubbing). */}
        {hover == null && (
          <circle cx={endX} cy={endY} r={3.5} fill={stroke} className="sparkline__dot" />
        )}
      </svg>

      {hovered && (
        <>
          <span
            className="sparkline__cursor"
            style={{ left: `${hoverXPct}%` }}
            aria-hidden="true"
          />
          <span
            className="sparkline__marker"
            style={{ left: `${hoverXPct}%`, top: `${hoverYPct}%`, background: stroke }}
            aria-hidden="true"
          />
          <span
            className={`sparkline__tip sparkline__tip--${tipAlign}`}
            style={{ left: `${hoverXPct}%` }}
            role="status"
          >
            <span className="sparkline__tip-amount">{formatAmount(hovered.value)}</span>
            <span className="sparkline__tip-time">{formatTime(hovered.t)}</span>
          </span>
        </>
      )}
    </div>
  );
}
