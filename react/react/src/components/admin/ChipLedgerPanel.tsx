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

interface AuditResponse {
  ledger_totals: LedgerTotals;
  actual_totals: ActualTotals;
  drift: number;
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

interface ChipLedgerPanelProps {
  embedded?: boolean;
}

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

  const fetchAll = useCallback(async () => {
    setError(null);
    const scope = sandboxId ? `?sandbox_id=${encodeURIComponent(sandboxId)}` : '';
    const recentScope = sandboxId
      ? `&sandbox_id=${encodeURIComponent(sandboxId)}`
      : '';
    try {
      const [auditResp, recentResp] = await Promise.all([
        adminAPI.fetch(`/api/admin/chip-ledger/audit${scope}`),
        adminAPI.fetch(`/api/admin/chip-ledger/recent?limit=20${recentScope}`),
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
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [sandboxId]);

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

        <section className="chip-ledger-card">
          <h3>By reason (all-time)</h3>
          <ReasonTable totals={byReason} />
        </section>

        <section className="chip-ledger-card">
          <h3>By reason (24h)</h3>
          <ReasonTable totals={byReason24h} />
        </section>
      </div>

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
            <td>{reason}</td>
            <td className={`amount ${amount > 0 ? 'pos' : amount < 0 ? 'neg' : ''}`}>
              {signed(amount)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
