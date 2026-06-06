import type { HoldingsHistoryResponse } from './types';
import {
  fmt,
  computeYTicks,
  CHART_TOP_N,
  CHART_HEIGHT,
  CHART_PAD_LEFT,
  CHART_PAD_RIGHT,
  CHART_PAD_TOP,
  CHART_PAD_BOTTOM,
  CHART_COLORS,
} from './ledgerUtils';

interface HoldingsChartProps {
  history: HoldingsHistoryResponse | null;
  highlightedEntity: string | null;
  onSelectEntity: (entityId: string | null) => void;
}

export function HoldingsChart({ history, highlightedEntity, onSelectEntity }: HoldingsChartProps) {
  if (history === null) {
    return <p className="chip-ledger-empty">Loading history…</p>;
  }
  if (history.requires_sandbox) {
    return <p className="chip-ledger-empty">Select a sandbox to chart net worth over time.</p>;
  }
  if (history.series.length === 0) {
    return <p className="chip-ledger-empty">No net-worth snapshots recorded yet in this window.</p>;
  }

  // Cap at the top-N by current net worth so the chart stays readable.
  // The dropped series still appear in the table below.
  const visibleSeries = history.series.slice(0, CHART_TOP_N);

  const sinceMs = new Date(history.since).getTime();
  const asOfMs = new Date(history.as_of).getTime();
  const xSpan = Math.max(1, asOfMs - sinceMs);

  let yMin = 0;
  let yMax = 0;
  for (const series of visibleSeries) {
    for (const point of series.points) {
      if (point.value < yMin) yMin = point.value;
      if (point.value > yMax) yMax = point.value;
    }
  }
  if (yMin === yMax) yMax = yMin + 1;
  const ySpan = yMax - yMin;

  // ResponsiveContainer-equivalent: the SVG fills its parent width
  // via `viewBox` + 100% width; we pick a fixed viewBox width so the
  // path math stays integer-friendly.
  const VB_WIDTH = 800;
  const innerW = VB_WIDTH - CHART_PAD_LEFT - CHART_PAD_RIGHT;
  const innerH = CHART_HEIGHT - CHART_PAD_TOP - CHART_PAD_BOTTOM;

  const xOf = (tIso: string) => {
    const ms = new Date(tIso).getTime();
    return CHART_PAD_LEFT + ((ms - sinceMs) / xSpan) * innerW;
  };
  const yOf = (v: number) => CHART_PAD_TOP + (1 - (v - yMin) / ySpan) * innerH;

  // Y-axis ticks: 4 evenly-spaced gridlines that include zero when
  // the series spans both positive and negative net flow.
  const yTicks = computeYTicks(yMin, yMax);

  return (
    <div className="chip-ledger-holdings-chart">
      <svg
        viewBox={`0 0 ${VB_WIDTH} ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Net worth over time"
      >
        {yTicks.map((tick) => {
          const y = yOf(tick);
          return (
            <g key={tick} className="chip-ledger-holdings-gridline">
              <line x1={CHART_PAD_LEFT} x2={VB_WIDTH - CHART_PAD_RIGHT} y1={y} y2={y} />
              <text x={CHART_PAD_LEFT - 6} y={y} textAnchor="end" dominantBaseline="central">
                {fmt(tick)}
              </text>
            </g>
          );
        })}
        {visibleSeries.map((series, idx) => {
          const color = CHART_COLORS[idx % CHART_COLORS.length];
          const isHighlighted = highlightedEntity === series.entity_id;
          const isDimmed = highlightedEntity !== null && !isHighlighted;
          // Net worth is a level, not a cumulative flow — start each line at
          // its first recorded snapshot (no zero pin). A single point still
          // renders as a dot via the line cap.
          const points = series.points.map((p) => ({ x: xOf(p.t), y: yOf(p.value) }));
          const d = points
            .map((pt, i) => `${i === 0 ? 'M' : 'L'}${pt.x.toFixed(1)},${pt.y.toFixed(1)}`)
            .join(' ');
          return (
            <path
              key={series.entity_id}
              d={d}
              fill="none"
              stroke={color}
              strokeWidth={isHighlighted ? 2.5 : 1.5}
              strokeOpacity={isDimmed ? 0.25 : 1}
              onClick={() => onSelectEntity(isHighlighted ? null : series.entity_id)}
              style={{ cursor: 'pointer' }}
            />
          );
        })}
        <line
          x1={CHART_PAD_LEFT}
          x2={VB_WIDTH - CHART_PAD_RIGHT}
          y1={CHART_PAD_TOP + innerH}
          y2={CHART_PAD_TOP + innerH}
          className="chip-ledger-holdings-axis"
        />
      </svg>
      <div className="chip-ledger-holdings-legend">
        {visibleSeries.map((series, idx) => {
          const color = CHART_COLORS[idx % CHART_COLORS.length];
          const isHighlighted = highlightedEntity === series.entity_id;
          return (
            <button
              key={series.entity_id}
              type="button"
              className={`chip-ledger-holdings-legend-item ${isHighlighted ? 'active' : ''}`}
              onClick={() => onSelectEntity(isHighlighted ? null : series.entity_id)}
            >
              <span className="swatch" style={{ background: color }} />
              <span className="label">{series.label}</span>
              <span className="value">{fmt(series.current_net_worth)}</span>
            </button>
          );
        })}
        {history.series_total > visibleSeries.length && (
          <span className="chip-ledger-holdings-legend-more">
            +{history.series_total - visibleSeries.length} more in table
          </span>
        )}
      </div>
    </div>
  );
}
