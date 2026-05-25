import { useEffect, useState, useCallback } from 'react';
import { adminAPI } from '../../utils/api';
import './ChipLedgerPanel.css';

interface LedgerTotals {
  chips_created: number;
  chips_destroyed: number;
  outstanding: number;
}

interface ActualTotals {
  player_bankrolls: number;
  ai_bankrolls_stored: number;
  ai_bankrolls_projected: number;
  uncommitted_ai_regen: number;
  cash_table_seats_ai: number;
  active_loans_principal: number;
  live_session_ai_stacks: number;
  actual_outstanding: number;
}

interface BankPool {
  reserves: number;
  deposits_total: number;
  draws_total: number;
  deposits_24h: number;
  draws_24h: number;
  net_flow_24h: number;
  deposit_reasons: string[];
  draw_reasons: string[];
}

interface AuditResponse {
  ledger_totals: LedgerTotals;
  actual_totals: ActualTotals;
  drift: number;
  bank_pool?: BankPool;
  by_reason: Record<string, number>;
  by_reason_window_24h: Record<string, number>;
  errors?: Record<string, string>;
  as_of: string;
}

interface LedgerEntry {
  entry_id: number;
  created_at: string;
  source: string;
  sink: string;
  amount: number;
  reason: string;
  context?: Record<string, unknown> | null;
}

interface SandboxRow {
  sandbox_id: string;
  owner_id: string;
  name: string;
  created_at: string;
}

interface HoldingsRow {
  entity_id: string;
  kind: 'ai' | 'player';
  id: string;
  name: string;
  sandbox_id: string | null;
  stored_chips: number;
  projected_chips: number;
  uncommitted_regen: number;
  last_regen_tick: string | null;
  chips_won: number;
  chips_lost: number;
  net_pnl: number;
}

interface HoldingsSnapshotResponse {
  rows: HoldingsRow[];
  as_of: string;
  sandbox_id: string | null;
}

interface HoldingsSeriesPoint {
  t: string;
  value: number;
  reason: string;
}

interface HoldingsSeries {
  entity_id: string;
  label: string;
  kind: 'ai' | 'player';
  total_net_flow: number;
  points: HoldingsSeriesPoint[];
}

interface HoldingsHistoryResponse {
  series: HoldingsSeries[];
  series_total: number;
  series_truncated_to: number;
  since: string;
  as_of: string;
  sandbox_id: string | null;
  days: number;
}

interface ChipLedgerPanelProps {
  embedded?: boolean;
}

const HISTORY_DAYS_OPTIONS = [7, 30, 90] as const;
const CHART_TOP_N = 8;
const CHART_HEIGHT = 240;
const CHART_PAD_LEFT = 56;
const CHART_PAD_RIGHT = 12;
const CHART_PAD_TOP = 12;
const CHART_PAD_BOTTOM = 28;

// Hand-picked palette for the multi-line chart. Mirrors the
// `chip-ledger-card` dark theme tokens — bright enough to read on
// near-black, distinct enough to tell 8 lines apart at a glance.
const CHART_COLORS = [
  '#d4a574', '#56d364', '#79b8ff', '#f6a5c0', '#f5d76e',
  '#a78bfa', '#5cdbd3', '#ff8c5a', '#b8b8b8', '#73c991',
];

const REFRESH_MS = 30_000;
const ALL_SANDBOXES = '';  // sentinel for the cross-sandbox admin view

function fmt(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  return n.toLocaleString();
}

function signed(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  return n > 0 ? `+${fmt(n)}` : fmt(n);
}

export function ChipLedgerPanel({ embedded = false }: ChipLedgerPanelProps) {
  const [audit, setAudit] = useState<AuditResponse | null>(null);
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [sandboxes, setSandboxes] = useState<SandboxRow[]>([]);
  const [sandboxId, setSandboxId] = useState<string>(ALL_SANDBOXES);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [holdings, setHoldings] = useState<HoldingsRow[] | null>(null);
  const [history, setHistory] = useState<HoldingsHistoryResponse | null>(null);
  const [historyDays, setHistoryDays] = useState<number>(30);
  const [highlightedEntity, setHighlightedEntity] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    setError(null);
    const scope = sandboxId ? `?sandbox_id=${encodeURIComponent(sandboxId)}` : '';
    const recentScope = sandboxId
      ? `&sandbox_id=${encodeURIComponent(sandboxId)}`
      : '';
    const historyScope = sandboxId
      ? `&sandbox_id=${encodeURIComponent(sandboxId)}`
      : '';
    try {
      const [auditResp, recentResp, holdingsResp, historyResp] = await Promise.all([
        adminAPI.fetch(`/api/admin/chip-ledger/audit${scope}`),
        adminAPI.fetch(`/api/admin/chip-ledger/recent?limit=20${recentScope}`),
        adminAPI.fetch(`/api/admin/chip-ledger/holdings${scope}`),
        adminAPI.fetch(
          `/api/admin/chip-ledger/holdings/history?days=${historyDays}${historyScope}`,
        ),
      ]);
      if (!auditResp.ok) {
        throw new Error(`Audit returned ${auditResp.status}`);
      }
      const auditData: AuditResponse = await auditResp.json();
      setAudit(auditData);
      if (recentResp.ok) {
        const recentData = await recentResp.json();
        setEntries(recentData.entries || []);
      }
      if (holdingsResp.ok) {
        const holdingsData: HoldingsSnapshotResponse = await holdingsResp.json();
        setHoldings(holdingsData.rows || []);
      }
      if (historyResp.ok) {
        const historyData: HoldingsHistoryResponse = await historyResp.json();
        setHistory(historyData);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [sandboxId, historyDays]);

  // Sandbox list is loaded once on mount — the set rarely changes
  // mid-session and refetching it on every audit refresh would be
  // wasted requests. The refresh button rerenders the audit only.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await adminAPI.fetch('/api/admin/sandboxes');
        if (!resp.ok || cancelled) return;
        const data = await resp.json();
        setSandboxes(data.sandboxes || []);
      } catch {
        // Sandbox listing is best-effort; the panel still works
        // without it (dropdown just shows "All sandboxes").
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, REFRESH_MS);
    return () => clearInterval(interval);
  }, [fetchAll]);

  if (loading && !audit) {
    return (
      <div className={`chip-ledger-panel ${embedded ? 'embedded' : ''}`}>
        <p>Loading chip-ledger audit…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className={`chip-ledger-panel ${embedded ? 'embedded' : ''}`}>
        <p className="chip-ledger-error">Audit error: {error}</p>
        <button onClick={fetchAll}>Retry</button>
      </div>
    );
  }

  if (!audit) return null;

  // Defend against an older backend that doesn't include all fields
  // (e.g. the Flask process hasn't been restarted to pick up the new
  // route, or a future schema rename). Render "—" for absent values
  // rather than crashing the whole panel.
  const ledgerTotals = audit.ledger_totals ?? {} as Partial<LedgerTotals>;
  const actualTotals = audit.actual_totals ?? {} as Partial<ActualTotals>;
  const byReason = audit.by_reason ?? {};
  const byReason24h = audit.by_reason_window_24h ?? {};
  const drift = audit.drift;
  const isMissing = drift === undefined || drift === null;
  const driftClass = isMissing
    ? 'drift-missing'
    : drift === 0 ? 'drift-zero' : drift > 0 ? 'drift-pos' : 'drift-neg';

  return (
    <div className={`chip-ledger-panel ${embedded ? 'embedded' : ''}`}>
      <div className="chip-ledger-header">
        <h2>Chip economy</h2>
        <label className="chip-ledger-sandbox-label">
          Sandbox
          <select
            className="chip-ledger-sandbox-select"
            value={sandboxId}
            onChange={(e) => setSandboxId(e.target.value)}
          >
            <option value={ALL_SANDBOXES}>All sandboxes (admin view)</option>
            {sandboxes.map(s => (
              <option key={s.sandbox_id} value={s.sandbox_id}>
                {s.name} — {s.sandbox_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <span className="chip-ledger-asof">as of {new Date(audit.as_of).toLocaleString()}</span>
        <button className="chip-ledger-refresh" onClick={fetchAll}>Refresh</button>
      </div>

      <div className={`chip-ledger-drift ${driftClass}`}>
        <span className="drift-label">drift</span>
        <span className="drift-value">{signed(drift)}</span>
        <span className="drift-help">
          {isMissing
            ? 'response missing drift field — backend may be stale (restart Flask?)'
            : drift === 0
              ? 'ledger and actuals agree'
              : 'ledger ≠ actual — bypass somewhere; v0 baseline may include pre-ledger chips'}
        </span>
      </div>

      {audit.errors && Object.keys(audit.errors).length > 0 && (
        <div className="chip-ledger-errors">
          <strong>Partial data:</strong>
          <ul>
            {Object.entries(audit.errors).map(([source, msg]) => (
              <li key={source}><code>{source}</code> — {msg}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="chip-ledger-grid">
        <section className="chip-ledger-card">
          <h3>Ledger view</h3>
          <dl>
            <dt>Created</dt><dd>{fmt(ledgerTotals.chips_created)}</dd>
            <dt>Destroyed</dt><dd>{fmt(ledgerTotals.chips_destroyed)}</dd>
            <dt>Outstanding</dt><dd>{fmt(ledgerTotals.outstanding)}</dd>
          </dl>
        </section>

        <section className="chip-ledger-card">
          <h3>Actual view</h3>
          <dl>
            <dt>Player bankrolls</dt><dd>{fmt(actualTotals.player_bankrolls)}</dd>
            <dt>AI bankrolls (stored)</dt><dd>{fmt(actualTotals.ai_bankrolls_stored)}</dd>
            <dt>Cash table AI seats</dt><dd>{fmt(actualTotals.cash_table_seats_ai)}</dd>
            <dt>Active loan principal</dt><dd>{fmt(actualTotals.active_loans_principal)}</dd>
            <dt>Live session AI stacks</dt><dd>{fmt(actualTotals.live_session_ai_stacks)}</dd>
            <dt><strong>Outstanding</strong></dt><dd><strong>{fmt(actualTotals.actual_outstanding)}</strong></dd>
            <dt className="muted">Uncommitted regen</dt><dd className="muted">{fmt(actualTotals.uncommitted_ai_regen)}</dd>
          </dl>
        </section>

        {audit.bank_pool && (
          <section className="chip-ledger-card">
            <h3>Bank pool</h3>
            <dl>
              <dt><strong>Reserves</strong></dt>
              <dd><strong>{fmt(audit.bank_pool.reserves)}</strong></dd>
              <dt>Deposits</dt><dd>{fmt(audit.bank_pool.deposits_total)}</dd>
              <dt>Draws</dt><dd>{fmt(audit.bank_pool.draws_total)}</dd>
              <dt className="muted">Net flow (24h)</dt>
              <dd className="muted">{signed(audit.bank_pool.net_flow_24h)}</dd>
              <dt className="muted">Deposits (24h)</dt>
              <dd className="muted">{fmt(audit.bank_pool.deposits_24h)}</dd>
              <dt className="muted">Draws (24h)</dt>
              <dd className="muted">{fmt(audit.bank_pool.draws_24h)}</dd>
            </dl>
            <p className="chip-ledger-bank-pool-caveat">
              Closed-economy recyclable reserve. Deposits come from{' '}
              <code>{audit.bank_pool.deposit_reasons.join(', ')}</code>;
              draws from <code>{audit.bank_pool.draw_reasons.join(', ')}</code>.
              Positive net flow → pool growing (vice outpacing tourists);
              negative → tourists draining the pool.
            </p>
          </section>
        )}

        {audit.bank_pool && (
          <BankPoolFlow pool={audit.bank_pool} byReason={byReason} />
        )}

        <section className="chip-ledger-card">
          <h3>By reason (all-time)</h3>
          <ReasonTable totals={byReason} />
        </section>

        <section className="chip-ledger-card">
          <h3>By reason (24h)</h3>
          <ReasonTable totals={byReason24h} />
        </section>
      </div>

      <section className="chip-ledger-card chip-ledger-holdings">
        <div className="chip-ledger-holdings-header">
          <h3>Player holdings</h3>
          <div className="chip-ledger-holdings-controls">
            <label className="chip-ledger-holdings-range">
              History
              <select
                value={historyDays}
                onChange={(e) => setHistoryDays(Number(e.target.value))}
              >
                {HISTORY_DAYS_OPTIONS.map(d => (
                  <option key={d} value={d}>{d}d</option>
                ))}
              </select>
            </label>
          </div>
        </div>

        <HoldingsChart
          history={history}
          highlightedEntity={highlightedEntity}
          onSelectEntity={setHighlightedEntity}
        />

        <HoldingsTable
          rows={holdings}
          highlightedEntity={highlightedEntity}
          onSelectEntity={setHighlightedEntity}
        />

        <p className="chip-ledger-holdings-caveat">
          The chart shows <em>net chips received from the central bank</em>
          {' '}per player (seed, regen, stake settlements, rake). Intra-table
          chip movement between players isn't tracked in the ledger and isn't
          plotted here — for true balance-over-time, a snapshot table would
          need to be added.
          <br />
          <em>Won / Lost / Net</em> columns aggregate cash-mode pair PnL
          {' '}(from <code>cash_pair_stats</code>) — scoped to the selected
          sandbox, or lifetime cross-sandbox in the "All sandboxes" view.
          Pre-v109 rows aren't migrated, so totals reflect activity from
          the v109 schema upgrade onward.
        </p>
      </section>

      <section className="chip-ledger-card chip-ledger-recent">
        <h3>Recent entries</h3>
        {entries.length === 0 ? (
          <p className="chip-ledger-empty">No entries yet — the ledger fires on cash-mode events.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Reason</th>
                <th>From</th>
                <th>To</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(e => (
                <tr key={e.entry_id}>
                  <td>{new Date(e.created_at).toLocaleString()}</td>
                  <td>{e.reason}</td>
                  <td>{e.source}</td>
                  <td>{e.sink}</td>
                  <td className="amount">{fmt(e.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

interface HoldingsTableProps {
  rows: HoldingsRow[] | null;
  highlightedEntity: string | null;
  onSelectEntity: (entityId: string | null) => void;
}

type SortKey =
  | 'name' | 'kind' | 'stored_chips' | 'projected_chips' | 'uncommitted_regen'
  | 'chips_won' | 'chips_lost' | 'net_pnl' | 'sandbox_id';
type SortDir = 'asc' | 'desc';

interface SortableHeaderProps {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey;
  currentDir: SortDir;
  align?: 'left' | 'right';
  onSort: (key: SortKey) => void;
}

function SortableHeader({ label, sortKey, currentKey, currentDir, align, onSort }: SortableHeaderProps) {
  const isActive = currentKey === sortKey;
  const arrow = isActive ? (currentDir === 'asc' ? '▲' : '▼') : '';
  return (
    <th
      className={`sortable ${align === 'right' ? 'amount-h' : ''} ${isActive ? 'active' : ''}`}
      onClick={() => onSort(sortKey)}
    >
      <span className="label">{label}</span>
      <span className="arrow">{arrow}</span>
    </th>
  );
}

function compareRows(a: HoldingsRow, b: HoldingsRow, key: SortKey, dir: SortDir): number {
  const av = a[key];
  const bv = b[key];
  // Nullable / string columns: push nulls to the bottom regardless of dir.
  if (av === null && bv === null) return 0;
  if (av === null) return 1;
  if (bv === null) return -1;
  let cmp: number;
  if (typeof av === 'number' && typeof bv === 'number') {
    cmp = av - bv;
  } else {
    cmp = String(av).localeCompare(String(bv));
  }
  return dir === 'asc' ? cmp : -cmp;
}

function HoldingsTable({ rows, highlightedEntity, onSelectEntity }: HoldingsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('projected_chips');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      // First click on a new column: numeric columns default to desc
      // (largest first — what admins usually want for chip counts);
      // string columns default to asc.
      const isNumeric = key !== 'name' && key !== 'kind' && key !== 'sandbox_id';
      setSortDir(isNumeric ? 'desc' : 'asc');
    }
  };

  if (rows === null) {
    return <p className="chip-ledger-empty">Loading holdings…</p>;
  }
  if (rows.length === 0) {
    return <p className="chip-ledger-empty">No bankroll rows in scope.</p>;
  }

  const sortedRows = [...rows].sort((a, b) => compareRows(a, b, sortKey, sortDir));

  return (
    <div className="chip-ledger-holdings-table-wrap">
      <table>
        <thead>
          <tr>
            <SortableHeader label="Player" sortKey="name" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
            <SortableHeader label="Kind" sortKey="kind" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
            <SortableHeader label="Stored" sortKey="stored_chips" currentKey={sortKey} currentDir={sortDir} align="right" onSort={handleSort} />
            <SortableHeader label="Projected" sortKey="projected_chips" currentKey={sortKey} currentDir={sortDir} align="right" onSort={handleSort} />
            <SortableHeader label="Δ regen" sortKey="uncommitted_regen" currentKey={sortKey} currentDir={sortDir} align="right" onSort={handleSort} />
            <SortableHeader label="Won*" sortKey="chips_won" currentKey={sortKey} currentDir={sortDir} align="right" onSort={handleSort} />
            <SortableHeader label="Lost*" sortKey="chips_lost" currentKey={sortKey} currentDir={sortDir} align="right" onSort={handleSort} />
            <SortableHeader label="Net*" sortKey="net_pnl" currentKey={sortKey} currentDir={sortDir} align="right" onSort={handleSort} />
            <SortableHeader label="Sandbox" sortKey="sandbox_id" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
          </tr>
        </thead>
        <tbody>
          {(() => {
            // Won/Lost/Net come from cash_pair_stats aggregated per
            // observer_id. The holdings table can hold multiple rows
            // per personality (one per sandbox) in the cross-sandbox
            // admin view, but the aggregate has only one entry per
            // observer_id — so showing the same PnL number on every
            // row would visually multi-count it. Render PnL only on
            // the first row encountered for each entity_id; the rest
            // get an em-dash with a hover hint. When the sandbox
            // dropdown is set to a specific sandbox each personality
            // already appears once, so this is a no-op there.
            const pnlShown = new Set<string>();
            return sortedRows.map(row => {
              const isHighlighted = highlightedEntity === row.entity_id;
              const drift = row.uncommitted_regen;
              const net = row.net_pnl;
              const showPnl = !pnlShown.has(row.entity_id);
              if (showPnl) pnlShown.add(row.entity_id);
              const dupeHint = showPnl ? undefined : 'Lifetime total shown on first row';
              return (
                <tr
                  key={`${row.entity_id}@${row.sandbox_id ?? ''}`}
                  className={isHighlighted ? 'highlighted' : ''}
                  onClick={() => onSelectEntity(isHighlighted ? null : row.entity_id)}
                >
                  <td>{row.name}</td>
                  <td>{row.kind === 'ai' ? 'AI' : 'Human'}</td>
                  <td className="amount">{fmt(row.stored_chips)}</td>
                  <td className="amount">{fmt(row.projected_chips)}</td>
                  <td className={`amount ${drift > 0 ? 'pos' : drift < 0 ? 'neg' : ''}`}>
                    {drift === 0 ? '—' : signed(drift)}
                  </td>
                  <td className="amount pos" title={dupeHint}>
                    {!showPnl || row.chips_won === 0 ? '—' : fmt(row.chips_won)}
                  </td>
                  <td className="amount neg" title={dupeHint}>
                    {!showPnl || row.chips_lost === 0 ? '—' : fmt(row.chips_lost)}
                  </td>
                  <td
                    className={`amount ${showPnl && net > 0 ? 'pos' : showPnl && net < 0 ? 'neg' : ''}`}
                    title={dupeHint}
                  >
                    {!showPnl || net === 0 ? '—' : signed(net)}
                  </td>
                  <td className="sandbox-cell">
                    {row.sandbox_id ? row.sandbox_id.slice(0, 8) : '—'}
                  </td>
                </tr>
              );
            });
          })()}
        </tbody>
      </table>
    </div>
  );
}

interface HoldingsChartProps {
  history: HoldingsHistoryResponse | null;
  highlightedEntity: string | null;
  onSelectEntity: (entityId: string | null) => void;
}

function HoldingsChart({ history, highlightedEntity, onSelectEntity }: HoldingsChartProps) {
  if (history === null) {
    return <p className="chip-ledger-empty">Loading history…</p>;
  }
  if (history.series.length === 0) {
    return (
      <p className="chip-ledger-empty">
        No central-bank events in the selected window.
      </p>
    );
  }

  // Cap at the top-N by absolute net flow so the chart stays
  // readable. The dropped series still appear in the table below.
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
  const yOf = (v: number) =>
    CHART_PAD_TOP + (1 - (v - yMin) / ySpan) * innerH;

  // Y-axis ticks: 4 evenly-spaced gridlines that include zero when
  // the series spans both positive and negative net flow.
  const yTicks = computeYTicks(yMin, yMax);

  return (
    <div className="chip-ledger-holdings-chart">
      <svg
        viewBox={`0 0 ${VB_WIDTH} ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Player holdings over time"
      >
        {yTicks.map(tick => {
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
          const points = [
            // Pin the start at zero so every line opens at the chart
            // origin instead of jumping in at its first event — matches
            // the cumulative-net-flow semantic ("since `since`").
            { x: xOf(history.since), y: yOf(0) },
            ...series.points.map(p => ({ x: xOf(p.t), y: yOf(p.value) })),
          ];
          const d = points.map((pt, i) =>
            `${i === 0 ? 'M' : 'L'}${pt.x.toFixed(1)},${pt.y.toFixed(1)}`,
          ).join(' ');
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
              <span className="value">{signed(series.total_net_flow)}</span>
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

function computeYTicks(yMin: number, yMax: number): number[] {
  // Four ticks across the range, rounded to a "nice" step so they
  // land on round numbers instead of arbitrary decimals.
  const span = yMax - yMin;
  if (span <= 0) return [yMin];
  const rough = span / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(Math.abs(rough))));
  const step = Math.ceil(rough / mag) * mag;
  const start = Math.floor(yMin / step) * step;
  const ticks: number[] = [];
  for (let t = start; t <= yMax + step / 2; t += step) {
    ticks.push(Math.round(t));
  }
  return ticks;
}

// Friendly labels for ledger reason codes. Unmapped reasons fall back to
// the raw code so a newly-added reason never disappears from the UI.
const REASON_LABELS: Record<string, string> = {
  player_seed: 'Player seed',
  ai_seed: 'AI seed',
  ai_regen: 'AI regen (passive, retired)',
  house_stake_issue: 'House stake issued',
  tourist_injection: 'Tourist injection',
  casino_seat_seed: 'Casino seat seed',
  side_hustle_earning: 'Side hustle',
  bank_pool_sim_seed: 'Bank pool sim seed',
  cap_clamp: 'Cap clamp (legacy)',
  house_stake_settle: 'House stake settle',
  table_rake: 'Table rake',
  bank_pool_deposit: 'Bank pool deposit',
  vice_spending: 'Vice spending',
  casino_seat_return: 'Casino seat return',
  forgive_balance: 'Forgive balance',
};

function labelFor(reason: string): string {
  return REASON_LABELS[reason] ?? reason;
}

// Grouped deposits→pool→draws view so the closed-economy loop is legible
// at a glance. Deposits are destructions (negative in by_reason); draws
// are creations (positive). We show absolute magnitudes per direction.
function BankPoolFlow({
  pool,
  byReason,
}: {
  pool: BankPool;
  byReason: Record<string, number>;
}) {
  const rows = (reasons: string[]) =>
    reasons
      .map((r) => ({ reason: r, amount: Math.abs(byReason[r] ?? 0) }))
      .filter((d) => d.amount !== 0)
      .sort((a, b) => b.amount - a.amount);

  const deposits = rows(pool.deposit_reasons);
  const draws = rows(pool.draw_reasons);
  const sum = (xs: { amount: number }[]) => xs.reduce((s, x) => s + x.amount, 0);

  const directionTable = (
    items: { reason: string; amount: number }[],
    sign: '+' | '-',
    totalLabel: string,
  ) =>
    items.length === 0 ? (
      <p className="chip-ledger-empty">none yet</p>
    ) : (
      <table>
        <tbody>
          {items.map((d) => (
            <tr key={d.reason}>
              <td title={d.reason}>{labelFor(d.reason)}</td>
              <td className={`amount ${sign === '+' ? 'pos' : 'neg'}`}>
                {sign}
                {fmt(d.amount)}
              </td>
            </tr>
          ))}
          <tr className="chip-ledger-pool-flow__subtotal">
            <td><strong>{totalLabel}</strong></td>
            <td className={`amount ${sign === '+' ? 'pos' : 'neg'}`}>
              <strong>{sign}{fmt(sum(items))}</strong>
            </td>
          </tr>
        </tbody>
      </table>
    );

  return (
    <section className="chip-ledger-card chip-ledger-pool-flow">
      <h3>Bank pool flow</h3>
      <h4>Deposits → pool</h4>
      {directionTable(deposits, '+', 'Total in')}
      <h4>Pool → draws</h4>
      {directionTable(draws, '-', 'Total out')}
      <p className="chip-ledger-bank-pool-caveat">
        Reserves = deposits − draws = <strong>{fmt(pool.reserves)}</strong>.
        Rake + vice feed the pool; the side hustle + tourist injection draw
        it down. A dry pool starves the side hustle (broke AIs stay broke
        until rake/vice refill it).
      </p>
    </section>
  );
}

function ReasonTable({ totals }: { totals: Record<string, number> }) {
  const entries = Object.entries(totals);
  if (entries.length === 0) {
    return <p className="chip-ledger-empty">No data in this window.</p>;
  }
  entries.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  return (
    <table>
      <tbody>
        {entries.map(([reason, amount]) => (
          <tr key={reason}>
            <td title={reason}>{labelFor(reason)}</td>
            <td className={`amount ${amount > 0 ? 'pos' : amount < 0 ? 'neg' : ''}`}>
              {signed(amount)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
