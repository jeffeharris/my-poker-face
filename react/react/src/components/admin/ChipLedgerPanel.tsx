import { useEffect, useState, useCallback, useRef } from 'react';
import { adminAPI } from '../../utils/api';
import { useAuth } from '../../hooks/useAuth';
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
  // Total chips controlled = projected bankroll + chips in play on a
  // cash-table seat. `seat_chips` is the in-play portion (scoped only).
  chips: number;
  seat_chips?: number;
  uncommitted_regen: number;
  last_regen_tick: string | null;
  // Net-worth block — present only in the scoped (single-sandbox) view.
  // net_worth = chips (incl. in-play) + receivable − outstanding.
  net_worth?: number;
  receivable?: number;
  outstanding?: number;
  staking_pnl?: number; // realized P&L from backing others (signed)
  vice_spent?: number;
  side_hustle_earned?: number;
  rake_paid?: number; // total rake contributed to the house
}

interface HoldingsSnapshotResponse {
  rows: HoldingsRow[];
  as_of: string;
  sandbox_id: string | null;
  // False in the cross-sandbox "All sandboxes" view → net worth omitted
  // (stakes are global per entity; chips are per-sandbox).
  net_worth_scoped: boolean;
}

interface HoldingsSeriesPoint {
  t: string;
  value: number; // net worth at this timestamp
}

interface HoldingsSeries {
  entity_id: string;
  label: string;
  kind: 'ai' | 'player';
  current_net_worth: number;
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
  // True when no sandbox is selected — net worth needs one, so the chart
  // shows a "select a sandbox" prompt instead of an empty plot.
  requires_sandbox: boolean;
}

interface LifecycleResponse {
  window_hours: number;
  // event name → count over the window (started/left_clean/left_ghost/swept/broken/...)
  events: Record<string, number>;
  // session_state → current count (active/paused/closed/broken/...)
  states: Record<string, number>;
  outstanding_broken: number;
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
  '#d4a574',
  '#56d364',
  '#79b8ff',
  '#f6a5c0',
  '#f5d76e',
  '#a78bfa',
  '#5cdbd3',
  '#ff8c5a',
  '#b8b8b8',
  '#73c991',
];

const REFRESH_MS = 30_000;
const ALL_SANDBOXES = ''; // sentinel for the cross-sandbox admin view

function fmt(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  return n.toLocaleString();
}

function signed(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  return n > 0 ? `+${fmt(n)}` : fmt(n);
}

export function ChipLedgerPanel({ embedded = false }: ChipLedgerPanelProps) {
  const { user } = useAuth();
  // Tracks whether the admin has manually changed the sandbox dropdown, so
  // the "default to your own sandbox" auto-select never fights a manual pick
  // (including an intentional "All sandboxes").
  const userChoseRef = useRef(false);
  const [audit, setAudit] = useState<AuditResponse | null>(null);
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [sandboxes, setSandboxes] = useState<SandboxRow[]>([]);
  const [sandboxId, setSandboxId] = useState<string>(ALL_SANDBOXES);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [holdings, setHoldings] = useState<HoldingsRow[] | null>(null);
  const [holdingsScoped, setHoldingsScoped] = useState<boolean>(false);
  const [history, setHistory] = useState<HoldingsHistoryResponse | null>(null);
  const [historyDays, setHistoryDays] = useState<number>(30);
  const [lifecycle, setLifecycle] = useState<LifecycleResponse | null>(null);
  const [highlightedEntity, setHighlightedEntity] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    setError(null);
    const scope = sandboxId ? `?sandbox_id=${encodeURIComponent(sandboxId)}` : '';
    // Same value as `scope` but as a trailing `&` param for URLs that already
    // carry a query string (recent's `limit`, history's `days`).
    const scopeParam = sandboxId ? `&sandbox_id=${encodeURIComponent(sandboxId)}` : '';
    try {
      const [auditResp, recentResp, holdingsResp, historyResp, lifecycleResp] = await Promise.all([
        adminAPI.fetch(`/api/admin/chip-ledger/audit${scope}`),
        adminAPI.fetch(`/api/admin/chip-ledger/recent?limit=20${scopeParam}`),
        adminAPI.fetch(`/api/admin/chip-ledger/holdings${scope}`),
        adminAPI.fetch(`/api/admin/chip-ledger/holdings/history?days=${historyDays}${scopeParam}`),
        adminAPI.fetch(`/api/admin/chip-ledger/lifecycle${scope}`),
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
        setHoldingsScoped(Boolean(holdingsData.net_worth_scoped));
      }
      if (historyResp.ok) {
        const historyData: HoldingsHistoryResponse = await historyResp.json();
        setHistory(historyData);
      }
      if (lifecycleResp.ok) {
        const lifecycleData: LifecycleResponse = await lifecycleResp.json();
        setLifecycle(lifecycleData);
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
    return () => {
      cancelled = true;
    };
  }, []);

  // Default the scope to the admin's OWN sandbox (fallback: the first one)
  // once the sandbox list + auth resolve. Net worth is gated behind a
  // selected sandbox, so leaving the default on "All sandboxes" would show
  // an empty chart + chips-only table — reads as broken. "All sandboxes" is
  // still one click away, and a manual pick is never overridden.
  useEffect(() => {
    if (userChoseRef.current || sandboxes.length === 0) return;
    const own = user ? sandboxes.find((s) => s.owner_id === user.id) : undefined;
    const target = own?.sandbox_id ?? sandboxes[0].sandbox_id;
    setSandboxId((prev) => (prev === target ? prev : target));
  }, [sandboxes, user]);

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
  const ledgerTotals = audit.ledger_totals ?? ({} as Partial<LedgerTotals>);
  const actualTotals = audit.actual_totals ?? ({} as Partial<ActualTotals>);
  const byReason = audit.by_reason ?? {};
  const byReason24h = audit.by_reason_window_24h ?? {};
  const drift = audit.drift;
  const isMissing = drift === undefined || drift === null;
  const driftClass = isMissing
    ? 'drift-missing'
    : drift === 0
      ? 'drift-zero'
      : drift > 0
        ? 'drift-pos'
        : 'drift-neg';

  return (
    <div className={`chip-ledger-panel ${embedded ? 'embedded' : ''}`}>
      <div className="chip-ledger-header">
        <h2>Chip economy</h2>
        <label className="chip-ledger-sandbox-label">
          Sandbox
          <select
            className="chip-ledger-sandbox-select"
            value={sandboxId}
            onChange={(e) => {
              userChoseRef.current = true;
              setSandboxId(e.target.value);
            }}
          >
            <option value={ALL_SANDBOXES}>All sandboxes (admin view)</option>
            {sandboxes.map((s) => (
              <option key={s.sandbox_id} value={s.sandbox_id}>
                {s.name} — {s.sandbox_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <span className="chip-ledger-asof">as of {new Date(audit.as_of).toLocaleString()}</span>
        <button className="chip-ledger-refresh" onClick={fetchAll}>
          Refresh
        </button>
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
              <li key={source}>
                <code>{source}</code> — {msg}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="chip-ledger-grid">
        <section className="chip-ledger-card">
          <h3>Ledger view</h3>
          <dl>
            <dt>Created</dt>
            <dd>{fmt(ledgerTotals.chips_created)}</dd>
            <dt>Destroyed</dt>
            <dd>{fmt(ledgerTotals.chips_destroyed)}</dd>
            <dt>Outstanding</dt>
            <dd>{fmt(ledgerTotals.outstanding)}</dd>
          </dl>
        </section>

        <section className="chip-ledger-card">
          <h3>Actual view</h3>
          <dl>
            <dt>Player bankrolls</dt>
            <dd>{fmt(actualTotals.player_bankrolls)}</dd>
            <dt>AI bankrolls (stored)</dt>
            <dd>{fmt(actualTotals.ai_bankrolls_stored)}</dd>
            <dt>Cash table AI seats</dt>
            <dd>{fmt(actualTotals.cash_table_seats_ai)}</dd>
            <dt>Active loan principal</dt>
            <dd>{fmt(actualTotals.active_loans_principal)}</dd>
            <dt>Live session AI stacks</dt>
            <dd>{fmt(actualTotals.live_session_ai_stacks)}</dd>
            <dt>
              <strong>Outstanding</strong>
            </dt>
            <dd>
              <strong>{fmt(actualTotals.actual_outstanding)}</strong>
            </dd>
            <dt className="muted">Uncommitted regen</dt>
            <dd className="muted">{fmt(actualTotals.uncommitted_ai_regen)}</dd>
          </dl>
        </section>

        {audit.bank_pool && (
          <section className="chip-ledger-card">
            <h3>Bank pool</h3>
            <dl>
              <dt>
                <strong>Reserves</strong>
              </dt>
              <dd>
                <strong>{fmt(audit.bank_pool.reserves)}</strong>
              </dd>
              <dt>Deposits</dt>
              <dd>{fmt(audit.bank_pool.deposits_total)}</dd>
              <dt>Draws</dt>
              <dd>{fmt(audit.bank_pool.draws_total)}</dd>
              <dt className="muted">Net flow (24h)</dt>
              <dd className="muted">{signed(audit.bank_pool.net_flow_24h)}</dd>
              <dt className="muted">Deposits (24h)</dt>
              <dd className="muted">{fmt(audit.bank_pool.deposits_24h)}</dd>
              <dt className="muted">Draws (24h)</dt>
              <dd className="muted">{fmt(audit.bank_pool.draws_24h)}</dd>
            </dl>
            <p className="chip-ledger-bank-pool-caveat">
              Closed-economy recyclable reserve. Deposits come from{' '}
              <code>{audit.bank_pool.deposit_reasons.join(', ')}</code>; draws from{' '}
              <code>{audit.bank_pool.draw_reasons.join(', ')}</code>. Positive net flow → pool
              growing (vice outpacing tourists); negative → tourists draining the pool.
            </p>
          </section>
        )}

        {audit.bank_pool && <BankPoolFlow pool={audit.bank_pool} byReason={byReason} />}

        <section className="chip-ledger-card">
          <h3>By reason (all-time)</h3>
          <ReasonTable totals={byReason} />
        </section>

        <section className="chip-ledger-card">
          <h3>By reason (24h)</h3>
          <ReasonTable totals={byReason24h} />
        </section>
      </div>

      {lifecycle && (
        <section className="chip-ledger-card chip-ledger-lifecycle">
          <h3>
            Session lifecycle{' '}
            <span className="chip-ledger-lifecycle__window">
              (events: last {lifecycle.window_hours}h · states: now)
            </span>
          </h3>
          <div className="chip-ledger-lifecycle__row">
            {(
              [
                ['started', 'Started'],
                ['left_clean', 'Left'],
                ['left_ghost', 'Left (ghost)'],
                ['swept', 'Swept'],
                ['broken', 'Broke'],
              ] as const
            ).map(([key, label]) => (
              <div key={key} className="chip-ledger-lifecycle__stat">
                <span className="chip-ledger-lifecycle__num">{lifecycle.events[key] ?? 0}</span>
                <span className="chip-ledger-lifecycle__label">{label}</span>
              </div>
            ))}
          </div>
          <div className="chip-ledger-lifecycle__row chip-ledger-lifecycle__states">
            {(
              [
                ['active', 'Active'],
                ['paused', 'Paused'],
                ['closed', 'Closed'],
              ] as const
            ).map(([key, label]) => (
              <div key={key} className="chip-ledger-lifecycle__stat">
                <span className="chip-ledger-lifecycle__num">{lifecycle.states[key] ?? 0}</span>
                <span className="chip-ledger-lifecycle__label">{label}</span>
              </div>
            ))}
            {/* Outstanding broken sessions = cleanup that couldn't converge.
                Highlighted because a non-zero value is the wedge class this
                whole lifecycle effort targets. */}
            <div
              className={
                'chip-ledger-lifecycle__stat' +
                (lifecycle.outstanding_broken > 0 ? ' chip-ledger-lifecycle__stat--alert' : '')
              }
            >
              <span className="chip-ledger-lifecycle__num">{lifecycle.outstanding_broken}</span>
              <span className="chip-ledger-lifecycle__label">Broken (outstanding)</span>
            </div>
          </div>
          <p className="chip-ledger-holdings-caveat">
            Lifecycle events from <code>cash_session_events</code>: <em>Started</em> at sit-down,{' '}
            <em>Left</em> on a clean cash-out, <em>Left (ghost)</em> when the game was gone from
            memory, <em>Swept</em> by the boot/watchdog reconciler, <em>Broke</em> when a teardown
            couldn't converge. <em>Broken (outstanding)</em> counts sessions stuck in that state now
            — it should normally be 0; a climbing value means orphans aren't self-healing.
          </p>
        </section>
      )}

      <section className="chip-ledger-card chip-ledger-holdings">
        <div className="chip-ledger-holdings-header">
          <h3>Player holdings</h3>
          <div className="chip-ledger-holdings-controls">
            <label className="chip-ledger-holdings-range">
              History
              <select value={historyDays} onChange={(e) => setHistoryDays(Number(e.target.value))}>
                {HISTORY_DAYS_OPTIONS.map((d) => (
                  <option key={d} value={d}>
                    {d}d
                  </option>
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
          scoped={holdingsScoped}
          highlightedEntity={highlightedEntity}
          onSelectEntity={setHighlightedEntity}
        />

        <p className="chip-ledger-holdings-caveat">
          <em>Net worth</em> = chips + stakes receivable − stakes outstanding, where <em>Chips</em>{' '}
          is off-table bankroll <em>plus</em> chips in play on a cash-table seat (hover Chips for
          the split). <em>Staking</em> is realized P&L from backing others (settled + defaulted
          stakes; open carries still show under Recv). <em>Vice</em>, <em>Side hustle</em>, and{' '}
          <em>Rake</em> are per-entity chip-ledger totals (rake = chips paid to the house). These
          require a selected sandbox: stakes are global per entity while chips are per-sandbox, so
          the "All sandboxes" view shows chips only.
          <br />
          The chart plots net worth <em>over time</em> from periodic snapshots the world ticker
          records (~every 10&nbsp;min). History accrues going forward — it can't be reconstructed
          retroactively, since chips won/lost between players never touch the central-bank ledger.
        </p>
      </section>

      <section className="chip-ledger-card chip-ledger-recent">
        <h3>Recent entries</h3>
        {entries.length === 0 ? (
          <p className="chip-ledger-empty">
            No entries yet — the ledger fires on cash-mode events.
          </p>
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
              {entries.map((e) => (
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
  // True when a sandbox is selected → show the net-worth columns.
  // False ("All sandboxes") → chips-only, since net worth needs a sandbox.
  scoped: boolean;
  highlightedEntity: string | null;
  onSelectEntity: (entityId: string | null) => void;
}

type SortKey =
  | 'name'
  | 'kind'
  | 'chips'
  | 'sandbox_id'
  | 'net_worth'
  | 'receivable'
  | 'outstanding'
  | 'staking_pnl'
  | 'vice_spent'
  | 'side_hustle_earned'
  | 'rake_paid';
type SortDir = 'asc' | 'desc';

// Scoped-only columns: meaningless (absent) in the All-sandboxes view.
const NET_WORTH_KEYS: ReadonlySet<SortKey> = new Set<SortKey>([
  'net_worth',
  'receivable',
  'outstanding',
  'staking_pnl',
  'vice_spent',
  'side_hustle_earned',
  'rake_paid',
]);
const STRING_KEYS: ReadonlySet<SortKey> = new Set<SortKey>(['name', 'kind', 'sandbox_id']);

interface SortableHeaderProps {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey;
  currentDir: SortDir;
  align?: 'left' | 'right';
  onSort: (key: SortKey) => void;
}

function SortableHeader({
  label,
  sortKey,
  currentKey,
  currentDir,
  align,
  onSort,
}: SortableHeaderProps) {
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
  // Coerce undefined (absent net-worth fields) and null to a sentinel that
  // sorts to the bottom regardless of direction.
  const av = a[key] ?? null;
  const bv = b[key] ?? null;
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

function HoldingsTable({ rows, scoped, highlightedEntity, onSelectEntity }: HoldingsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('net_worth');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      // Numeric columns default to desc (largest first); strings to asc.
      setSortDir(STRING_KEYS.has(key) ? 'asc' : 'desc');
    }
  };

  if (rows === null) {
    return <p className="chip-ledger-empty">Loading holdings…</p>;
  }
  if (rows.length === 0) {
    return <p className="chip-ledger-empty">No bankroll rows in scope.</p>;
  }

  // In the unscoped view the net-worth columns are absent, so fall back to
  // chips for the active sort if a net-worth column was selected.
  const effectiveSortKey: SortKey = !scoped && NET_WORTH_KEYS.has(sortKey) ? 'chips' : sortKey;
  const sortedRows = [...rows].sort((a, b) => compareRows(a, b, effectiveSortKey, sortDir));

  return (
    <div className="chip-ledger-holdings-table-wrap">
      {!scoped && (
        <p className="chip-ledger-holdings-scope-note">
          Select a sandbox to see net worth, stakes, vice, and side-hustle — stakes are global per
          entity, so they aren't shown cross-sandbox.
        </p>
      )}
      <table>
        <thead>
          <tr>
            <SortableHeader
              label="Player"
              sortKey="name"
              currentKey={sortKey}
              currentDir={sortDir}
              onSort={handleSort}
            />
            <SortableHeader
              label="Kind"
              sortKey="kind"
              currentKey={sortKey}
              currentDir={sortDir}
              onSort={handleSort}
            />
            {scoped && (
              <SortableHeader
                label="Net worth"
                sortKey="net_worth"
                currentKey={sortKey}
                currentDir={sortDir}
                align="right"
                onSort={handleSort}
              />
            )}
            <SortableHeader
              label="Chips"
              sortKey="chips"
              currentKey={sortKey}
              currentDir={sortDir}
              align="right"
              onSort={handleSort}
            />
            {scoped && (
              <>
                <SortableHeader
                  label="Recv"
                  sortKey="receivable"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Owed"
                  sortKey="outstanding"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Staking"
                  sortKey="staking_pnl"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Vice"
                  sortKey="vice_spent"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Side hustle"
                  sortKey="side_hustle_earned"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Rake"
                  sortKey="rake_paid"
                  currentKey={sortKey}
                  currentDir={sortDir}
                  align="right"
                  onSort={handleSort}
                />
              </>
            )}
            <SortableHeader
              label="Sandbox"
              sortKey="sandbox_id"
              currentKey={sortKey}
              currentDir={sortDir}
              onSort={handleSort}
            />
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row) => {
            const isHighlighted = highlightedEntity === row.entity_id;
            const netWorth = row.net_worth ?? 0;
            return (
              <tr
                key={`${row.entity_id}@${row.sandbox_id ?? ''}`}
                className={isHighlighted ? 'highlighted' : ''}
                onClick={() => onSelectEntity(isHighlighted ? null : row.entity_id)}
              >
                <td>{row.name}</td>
                <td>{row.kind === 'ai' ? 'AI' : 'Human'}</td>
                {scoped && (
                  <td className={`amount ${netWorth > 0 ? 'pos' : netWorth < 0 ? 'neg' : ''}`}>
                    {fmt(netWorth)}
                  </td>
                )}
                <td
                  className="amount"
                  title={
                    row.seat_chips
                      ? `${fmt(row.projected_chips)} bankroll + ${fmt(row.seat_chips)} in play`
                      : undefined
                  }
                >
                  {fmt(row.chips)}
                </td>
                {scoped && (
                  <>
                    <td className="amount pos">{row.receivable ? fmt(row.receivable) : '—'}</td>
                    <td className="amount neg">{row.outstanding ? fmt(row.outstanding) : '—'}</td>
                    <td
                      className={`amount ${(row.staking_pnl ?? 0) > 0 ? 'pos' : (row.staking_pnl ?? 0) < 0 ? 'neg' : ''}`}
                    >
                      {row.staking_pnl ? signed(row.staking_pnl) : '—'}
                    </td>
                    <td className="amount neg">{row.vice_spent ? fmt(row.vice_spent) : '—'}</td>
                    <td className="amount pos">
                      {row.side_hustle_earned ? fmt(row.side_hustle_earned) : '—'}
                    </td>
                    <td className="amount neg">{row.rake_paid ? fmt(row.rake_paid) : '—'}</td>
                  </>
                )}
                <td className="sandbox-cell">
                  {row.sandbox_id ? row.sandbox_id.slice(0, 8) : '—'}
                </td>
              </tr>
            );
          })}
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
function BankPoolFlow({ pool, byReason }: { pool: BankPool; byReason: Record<string, number> }) {
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
    totalLabel: string
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
            <td>
              <strong>{totalLabel}</strong>
            </td>
            <td className={`amount ${sign === '+' ? 'pos' : 'neg'}`}>
              <strong>
                {sign}
                {fmt(sum(items))}
              </strong>
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
        Reserves = deposits − draws = <strong>{fmt(pool.reserves)}</strong>. Rake + vice feed the
        pool; the side hustle + tourist injection draw it down. A dry pool starves the side hustle
        (broke AIs stay broke until rake/vice refill it).
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
