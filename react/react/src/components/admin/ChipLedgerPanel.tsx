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
  ai_bankrolls_projected: number;
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

interface ChipLedgerPanelProps {
  embedded?: boolean;
}

const REFRESH_MS = 30_000;

function fmt(n: number): string {
  return n.toLocaleString();
}

function signed(n: number): string {
  return n > 0 ? `+${fmt(n)}` : fmt(n);
}

export function ChipLedgerPanel({ embedded = false }: ChipLedgerPanelProps) {
  const [audit, setAudit] = useState<AuditResponse | null>(null);
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    setError(null);
    try {
      const [auditResp, recentResp] = await Promise.all([
        adminAPI.fetch('/api/admin/chip-ledger/audit'),
        adminAPI.fetch('/api/admin/chip-ledger/recent?limit=20'),
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

  const driftClass = audit.drift === 0 ? 'drift-zero' : audit.drift > 0 ? 'drift-pos' : 'drift-neg';

  return (
    <div className={`chip-ledger-panel ${embedded ? 'embedded' : ''}`}>
      <div className="chip-ledger-header">
        <h2>Chip economy</h2>
        <span className="chip-ledger-asof">as of {new Date(audit.as_of).toLocaleString()}</span>
        <button className="chip-ledger-refresh" onClick={fetchAll}>Refresh</button>
      </div>

      <div className={`chip-ledger-drift ${driftClass}`}>
        <span className="drift-label">drift</span>
        <span className="drift-value">{signed(audit.drift)}</span>
        <span className="drift-help">
          {audit.drift === 0
            ? 'ledger and actuals agree'
            : 'ledger ≠ actual — bypass somewhere; v0 baseline may include pre-ledger chips'}
        </span>
      </div>

      <div className="chip-ledger-grid">
        <section className="chip-ledger-card">
          <h3>Ledger view</h3>
          <dl>
            <dt>Created</dt><dd>{fmt(audit.ledger_totals.chips_created)}</dd>
            <dt>Destroyed</dt><dd>{fmt(audit.ledger_totals.chips_destroyed)}</dd>
            <dt>Outstanding</dt><dd>{fmt(audit.ledger_totals.outstanding)}</dd>
          </dl>
        </section>

        <section className="chip-ledger-card">
          <h3>Actual view</h3>
          <dl>
            <dt>Player bankrolls</dt><dd>{fmt(audit.actual_totals.player_bankrolls)}</dd>
            <dt>AI bankrolls (projected)</dt><dd>{fmt(audit.actual_totals.ai_bankrolls_projected)}</dd>
            <dt>Cash table AI seats</dt><dd>{fmt(audit.actual_totals.cash_table_seats_ai)}</dd>
            <dt>Active loan principal</dt><dd>{fmt(audit.actual_totals.active_loans_principal)}</dd>
            <dt>Live session AI stacks</dt><dd>{fmt(audit.actual_totals.live_session_ai_stacks)}</dd>
            <dt><strong>Outstanding</strong></dt><dd><strong>{fmt(audit.actual_totals.actual_outstanding)}</strong></dd>
          </dl>
        </section>

        <section className="chip-ledger-card">
          <h3>By reason (all-time)</h3>
          <ReasonTable totals={audit.by_reason} />
        </section>

        <section className="chip-ledger-card">
          <h3>By reason (24h)</h3>
          <ReasonTable totals={audit.by_reason_window_24h} />
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
