/**
 * ArchetypeReviewPanel — "is each archetype behaving like its label?".
 *
 * Reads GET /api/admin/archetype-review/summary?mode=cash. For every tiered
 * archetype (nit/rock/tag/lag/maniac/calling_station/weak_fish) it shows the
 * actual measured behavioral stats (VPIP, PFR, 3-bet, 4-bet, fold-to-3bet, AF,
 * all-in%) against the target range from poker/archetype_targets.py, colored
 * pass / warn / fail. The shaping dashboard: tune AIs into readable reads.
 */

import { useCallback, useEffect, useState } from 'react';
import { Target, RefreshCw } from 'lucide-react';
import { adminAPI } from '../../utils/api';
import { logger } from '../../utils/logger';
import './ArchetypeReviewPanel.css';

type Status = 'pass' | 'warn' | 'fail' | 'low_n' | 'no_data' | 'no_target';

interface StatCell {
  actual: number | null;
  sample: number;
  target: [number, number] | null;
  status: Status;
}
interface ArchRow {
  archetype: string;
  is_production: boolean;
  hands: number;
  stats: Record<string, StatCell>;
}
interface Summary {
  mode: string;
  window: string;
  supports_window: boolean;
  stat_order: string[];
  stat_labels: Record<string, string>;
  archetypes: ArchRow[];
  total_decisions: number;
}

type Mode = 'cash' | 'tournament' | 'all';
const MODES: Mode[] = ['cash', 'tournament', 'all'];
type Source = 'live' | 'sim';
const SOURCES: { id: Source; label: string }[] = [
  { id: 'live', label: 'Live (you in)' },
  { id: 'sim', label: 'Sim (AI-only)' },
];
// Time window — LIVE only (filters on created_at). Sim reads cumulative
// counters, so it is locked to all-time.
type TimeWindow = '1h' | '24h' | '7d' | '30d' | 'all';
const WINDOWS: { id: TimeWindow; label: string }[] = [
  { id: '1h', label: '1h' },
  { id: '24h', label: '24h' },
  { id: '7d', label: '7d' },
  { id: '30d', label: '30d' },
  { id: 'all', label: 'All' },
];

const fmt = (s: StatCell): string => (s.actual === null ? '—' : `${s.actual}`);
const fmtTarget = (t: [number, number] | null): string => (t ? `${t[0]}–${t[1]}` : '—');

interface Props {
  embedded?: boolean;
}

export function ArchetypeReviewPanel({ embedded = false }: Props) {
  const [data, setData] = useState<Summary | null>(null);
  const [source, setSource] = useState<Source>('live');
  const [mode, setMode] = useState<Mode>('cash');
  const [timeWindow, setTimeWindow] = useState<TimeWindow>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (s: Source, m: Mode, w: TimeWindow) => {
    setLoading(true);
    setError(null);
    // Sim is cumulative → always all-time (the backend ignores window for sim).
    const effWindow = s === 'sim' ? 'all' : w;
    try {
      const resp = await adminAPI.fetch(
        `/api/admin/archetype-review/summary?source=${s}&mode=${m}&window=${effWindow}`
      );
      if (!resp.ok) throw new Error(`summary ${resp.status}`);
      setData(await resp.json());
    } catch (err) {
      logger.error('Failed to load archetype review:', err);
      setError('Could not load archetype review.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(source, mode, timeWindow);
  }, [load, source, mode, timeWindow]);

  return (
    <div className={embedded ? 'arp arp--embedded' : 'arp'}>
      <div className="arp-header">
        <div className="arp-title">
          <Target size={20} />
          <h2>Archetype Review</h2>
        </div>
        <div className="arp-controls">
          <div className="arp-modes">
            {SOURCES.map((s) => (
              <button
                key={s.id}
                className={`arp-mode ${source === s.id ? 'arp-mode--active' : ''}`}
                onClick={() => setSource(s.id)}
              >
                {s.label}
              </button>
            ))}
          </div>
          <div
            className="arp-modes"
            title={source === 'sim' ? 'Sim is cash-only (AI-vs-AI background games)' : ''}
          >
            {MODES.map((m) => {
              // Sim is cash-only — keep the toggle visible (don't vanish it) but
              // disabled, with 'cash' shown active, so the layout doesn't jump.
              const active = source === 'sim' ? m === 'cash' : mode === m;
              return (
                <button
                  key={m}
                  className={`arp-mode ${active ? 'arp-mode--active' : ''}`}
                  onClick={() => source === 'live' && setMode(m)}
                  disabled={source === 'sim'}
                >
                  {m}
                </button>
              );
            })}
          </div>
          <div
            className="arp-modes"
            title={
              source === 'sim'
                ? 'Sim counters are cumulative — all-time only (windowing needs snapshots)'
                : 'Time window (live decisions, by recency)'
            }
          >
            {WINDOWS.map((w) => {
              // Sim can't be windowed (cumulative counters) — lock to 'All',
              // disabled, mirroring the mode toggle so the layout stays put.
              const active = source === 'sim' ? w.id === 'all' : timeWindow === w.id;
              return (
                <button
                  key={w.id}
                  className={`arp-mode ${active ? 'arp-mode--active' : ''}`}
                  onClick={() => source === 'live' && setTimeWindow(w.id)}
                  disabled={source === 'sim'}
                >
                  {w.label}
                </button>
              );
            })}
          </div>
          <button
            className="arp-refresh"
            onClick={() => load(source, mode, timeWindow)}
            disabled={loading}
          >
            <RefreshCw size={14} className={loading ? 'arp-spin' : ''} />
          </button>
        </div>
      </div>

      <p className="arp-sub">
        Actual behavior vs target range per archetype. Green = on-target, amber = close, red = off.{' '}
        {data ? `${data.total_decisions.toLocaleString()} decisions` : ''}
        {data && source === 'live' && timeWindow !== 'all'
          ? ` in the last ${WINDOWS.find((w) => w.id === timeWindow)?.label}.`
          : '.'}{' '}
        {source === 'sim'
          ? 'Sim = the background AI-vs-AI cash games (you are NOT in these) — the clean archetype signal. Cumulative counters, so all-time only.'
          : 'Live = games you played in. Tiered-bot decisions only (the LLM path carries no archetype).'}
      </p>

      {error && <div className="arp-error">{error}</div>}
      {loading && !data && <div className="arp-loading">Loading…</div>}

      {data && (
        <div className="arp-table-wrap">
          <table className="arp-table">
            <thead>
              <tr>
                <th className="arp-arch-col">Archetype</th>
                <th className="arp-hands-col">Hands</th>
                {data.stat_order.map((s) => (
                  <th key={s}>{data.stat_labels[s]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.archetypes.map((row) => (
                <tr key={row.archetype} className={row.is_production ? '' : 'arp-extra'}>
                  <td className="arp-arch-col">{row.archetype}</td>
                  <td className="arp-hands-col">{row.hands.toLocaleString()}</td>
                  {data.stat_order.map((s) => {
                    const cell = row.stats[s];
                    return (
                      <td
                        key={s}
                        className={`arp-cell arp-${cell.status}`}
                        title={`actual ${cell.actual ?? '—'} · target ${fmtTarget(
                          cell.target
                        )} · n=${cell.sample} · ${cell.status}`}
                      >
                        <span className="arp-actual">{fmt(cell)}</span>
                        <span className="arp-target">{fmtTarget(cell.target)}</span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
