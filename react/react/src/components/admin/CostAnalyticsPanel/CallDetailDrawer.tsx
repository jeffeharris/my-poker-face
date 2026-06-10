import { useEffect, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { adminFetch } from '../../../utils/api';
import type { CallsResponse, UsageCall, CostRange } from './types';
import { callTypeLabel, fmtCost, fmtCount } from './costUtils';

interface CallDetailDrawerProps {
  range: CostRange;
  ownerId?: string;
  callType?: string;
  gameId?: string;
  /** Human-readable title for the drawer header. */
  title: string;
  onClose: () => void;
}

/**
 * Slide-in drawer listing raw api_usage rows for a given owner / call-type
 * filter — the deepest drill level. Rendered through a portal to `body` so it
 * escapes the admin shell's fixed-position header stacking context (see the
 * modal-portal-stacking memo).
 */
export function CallDetailDrawer({
  range,
  ownerId,
  callType,
  gameId,
  title,
  onClose,
}: CallDetailDrawerProps) {
  const [calls, setCalls] = useState<UsageCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ range, limit: '200' });
      if (ownerId) params.set('owner_id', ownerId);
      if (callType) params.set('call_type', callType);
      if (gameId) params.set('game_id', gameId);
      const resp = await adminFetch(`/api/admin/cost-analytics/calls?${params.toString()}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: CallsResponse = await resp.json();
      setCalls(data.calls);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [range, ownerId, callType, gameId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return createPortal(
    <div className="cost-drawer-overlay" onClick={onClose}>
      <div className="cost-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="cost-drawer-header">
          <div>
            <h3>{title}</h3>
            <span className="cost-drawer-sub">
              Latest {calls.length} calls · {range}
            </span>
          </div>
          <button className="cost-drawer-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        {loading && <div className="cost-drawer-status">Loading…</div>}
        {error && <div className="cost-drawer-status cost-error">Error: {error}</div>}

        {!loading && !error && calls.length === 0 && (
          <div className="cost-drawer-status">No calls in this range.</div>
        )}

        {!loading && !error && calls.length > 0 && (
          <div className="cost-table-wrap">
            <table className="cost-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Owner</th>
                  <th>Call type</th>
                  <th>Model</th>
                  <th className="num">In</th>
                  <th className="num">Out</th>
                  <th className="num">Latency</th>
                  <th>Status</th>
                  <th className="num">Cost</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((c) => (
                  <tr key={c.id} className={c.status === 'error' ? 'row-error' : undefined}>
                    <td className="cost-nowrap">{c.created_at?.replace('T', ' ').slice(0, 19)}</td>
                    <td className="cost-ellipsis" title={c.owner_id ?? ''}>
                      {c.owner_id ?? '(system)'}
                    </td>
                    <td>{callTypeLabel(c.call_type)}</td>
                    <td className="cost-ellipsis" title={`${c.provider}/${c.model}`}>
                      {c.model}
                    </td>
                    <td className="num">
                      {c.image_count > 0 ? `${c.image_count}🖼` : fmtCount(c.input_tokens)}
                    </td>
                    <td className="num">{fmtCount(c.output_tokens)}</td>
                    <td className="num">{c.latency_ms != null ? `${c.latency_ms}ms` : '—'}</td>
                    <td>
                      <span className={`cost-pill cost-pill-${c.status}`}>{c.status}</span>
                    </td>
                    <td className="num">{fmtCost(c.estimated_cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
