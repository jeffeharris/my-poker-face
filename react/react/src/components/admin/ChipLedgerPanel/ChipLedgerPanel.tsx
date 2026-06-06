import { useEffect, useState, useCallback, useRef } from 'react';
import { adminAPI } from '../../../utils/api';
import { useAuth } from '../../../hooks/useAuth';
import type {
  AuditResponse,
  LedgerEntry,
  SandboxRow,
  HoldingsRow,
  HoldingsHistoryResponse,
  LifecycleResponse,
  LedgerTotals,
  ActualTotals,
  ChipLedgerPanelProps,
} from './types';
import { fmt, signed, HISTORY_DAYS_OPTIONS, REFRESH_MS, ALL_SANDBOXES } from './ledgerUtils';
import { HoldingsChart } from './HoldingsChart';
import { HoldingsTable } from './HoldingsTable';
import { BankPoolFlow } from './BankPoolFlow';
import { ReasonTable } from './ReasonTable';
import './ChipLedgerPanel.css';

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
        const holdingsData = await holdingsResp.json();
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
        <span
          className="chip-ledger-ticks"
          title={
            sandboxId === ALL_SANDBOXES
              ? 'World ticks summed across all sandboxes — a maturity gauge for the economy.'
              : 'World ticks run for this sandbox. Wealth concentration reads differently early (a few hundred ticks) vs. a mature economy (thousands).'
          }
        >
          world ticks: {audit.world_ticks == null ? '—' : audit.world_ticks.toLocaleString()}
        </span>
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
