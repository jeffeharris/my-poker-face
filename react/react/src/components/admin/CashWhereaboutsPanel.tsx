/**
 * CashWhereaboutsPanel — admin world-state monitor + stuck tripwire.
 *
 * The unfiltered twin of the player's "Who's around" drawer. Lists every
 * trackable AI (seated / idle / side hustle / vice) for a sandbox — or
 * across all sandboxes — and surfaces invariant violations up top:
 * double-seats, seated-and-idle split-brain, overdue returns, stale idle,
 * orphan pids. It's a live tripwire for the ghost-seat / cold-load bug
 * classes that keep recurring in cash mode.
 *
 * Reads GET /api/admin/cash/whereabouts. Sandbox scoping mirrors
 * ChipLedgerPanel (own sandbox by default; "All sandboxes" = a
 * cross-world scan).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { adminAPI } from '../../utils/api';
import { useAuth } from '../../hooks/useAuth';
import type { WhereaboutsPerson } from '../cash/types';
import './CashWhereaboutsPanel.css';

interface SandboxRow {
  sandbox_id: string;
  owner_id: string;
  name: string;
}

interface WhereaboutsAdminResponse {
  people: WhereaboutsPerson[];
  stuck_count: number;
  total: number;
  sandbox_id: string | null;
}

const ALL_SANDBOXES = ''; // sentinel for the cross-sandbox scan
const REFRESH_MS = 15_000;

const STATUS_LABEL: Record<string, string> = {
  seated: 'Seated',
  idle: 'Idle',
  side_hustle: 'Side hustle',
  vice: 'Vice',
  unknown: 'Unknown',
};

const FLAG_LABEL: Record<string, string> = {
  // hard (stuck)
  double_seat: 'double seat',
  seated_and_idle: 'seated + idle',
  seated_and_offgrid: 'seated + off-grid',
  unknown_personality: 'orphan pid',
  no_bankroll: 'no bankroll',
  // soft (watch)
  overdue_hustle: 'overdue hustle',
  overdue_vice: 'overdue vice',
  stale_idle: 'stale idle',
  seated_too_long: 'parked too long',
};

function fmtDuration(seconds: number | null): string {
  if (seconds == null) return '—';
  const s = Math.max(0, Math.abs(seconds));
  if (s >= 3600) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
  if (s >= 60) return `${Math.floor(s / 60)}m`;
  return `${s}s`;
}

function fmtChips(n: number | null): string {
  return n == null ? '—' : n.toLocaleString();
}

/** Location / activity summary for the "Where / what" column. */
function whereCell(p: WhereaboutsPerson): string {
  switch (p.status) {
    case 'seated': {
      const label = p.table_name ? `${p.table_name} (${p.stake_label})` : `${p.stake_label} table`;
      const seat = p.seat_index != null ? ` · seat ${p.seat_index}` : '';
      return `${label}${seat}`;
    }
    case 'idle':
      return p.reason === 'stake_up_queued' && p.target_stake
        ? `idle → ${p.target_stake}`
        : `idle (${p.reason ?? '?'})`;
    case 'side_hustle':
    case 'vice':
      return p.narration || p.status;
    default:
      return '—';
  }
}

/** Timing column: idle-for, or returns-in / OVERDUE for hustle & vice. */
function timeCell(p: WhereaboutsPerson): { text: string; overdue: boolean } {
  if (p.status === 'side_hustle' || p.status === 'vice') {
    if (p.seconds_remaining == null) return { text: '—', overdue: false };
    if (p.seconds_remaining <= 0) {
      return { text: `OVERDUE ${fmtDuration(p.seconds_remaining)}`, overdue: true };
    }
    return { text: `back in ${fmtDuration(p.seconds_remaining)}`, overdue: false };
  }
  if (p.status === 'idle') {
    return { text: `idle ${fmtDuration(p.seconds_in_state)}`, overdue: false };
  }
  if (p.status === 'seated') {
    // seconds_in_state = time parked at the current table; null on legacy
    // seats saved before seated_at existed. Flag the overdue style when
    // the "parked too long" watch flag fired so it stands out.
    if (p.seconds_in_state == null) return { text: '—', overdue: false };
    return {
      text: `seated ${fmtDuration(p.seconds_in_state)}`,
      overdue: (p.watch ?? []).includes('seated_too_long'),
    };
  }
  return { text: '—', overdue: false };
}

/** Energy axis (0..1) as a small bar — drained AIs read red, fresh ones green. */
function EnergyBar({ energy }: { energy: number | null }) {
  if (energy == null) return <span className="cwp-muted">—</span>;
  const pct = Math.round(Math.max(0, Math.min(1, energy)) * 100);
  const level = energy < 0.34 ? 'low' : energy < 0.67 ? 'mid' : 'high';
  return (
    <div className="cwp-energy" title={`energy ${pct}%`}>
      <div className="cwp-energy__track">
        <div
          className={`cwp-energy__fill cwp-energy__fill--${level}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="cwp-energy__pct">{pct}%</span>
    </div>
  );
}

function FlagBadges({ flags, kind }: { flags: string[]; kind: 'stuck' | 'watch' }) {
  if (!flags.length) return null;
  return (
    <span className="cwp-flags">
      {flags.map((f) => (
        <span key={f} className={`cwp-flag cwp-flag--${kind}`} title={f}>
          {FLAG_LABEL[f] ?? f}
        </span>
      ))}
    </span>
  );
}

function PersonRow({ person, showSandbox }: { person: WhereaboutsPerson; showSandbox: boolean }) {
  const time = timeCell(person);
  const stuck = person.stuck ?? [];
  const watch = person.watch ?? [];
  const rowClass = stuck.length
    ? 'cwp-row cwp-row--stuck'
    : watch.length
      ? 'cwp-row cwp-row--watch'
      : 'cwp-row';
  return (
    <tr className={rowClass}>
      <td className="cwp-name">
        {person.name}
        <FlagBadges flags={stuck} kind="stuck" />
        <FlagBadges flags={watch} kind="watch" />
      </td>
      <td>
        <span className={`cwp-status cwp-status--${person.status}`}>
          {STATUS_LABEL[person.status] ?? person.status}
        </span>
      </td>
      <td className="cwp-where">{whereCell(person)}</td>
      <td className={time.overdue ? 'cwp-time cwp-time--overdue' : 'cwp-time'}>{time.text}</td>
      <td className="cwp-energy-cell">
        <EnergyBar energy={person.energy} />
      </td>
      <td className="cwp-num">{fmtChips(person.bankroll)}</td>
      <td className="cwp-num">
        {person.met ? (
          <span title={`${person.hands_played} hands`}>
            {person.net_pnl > 0 ? '+' : person.net_pnl < 0 ? '−' : ''}
            {Math.abs(person.net_pnl).toLocaleString()}
          </span>
        ) : (
          <span className="cwp-muted">—</span>
        )}
      </td>
      {showSandbox && (
        <td className="cwp-sandbox">{person.sandbox_owner_id || person.sandbox_id}</td>
      )}
    </tr>
  );
}

interface CashWhereaboutsPanelProps {
  embedded?: boolean;
}

export function CashWhereaboutsPanel({ embedded = false }: CashWhereaboutsPanelProps) {
  const { user } = useAuth();
  const userChoseRef = useRef(false);
  const [data, setData] = useState<WhereaboutsAdminResponse | null>(null);
  const [sandboxes, setSandboxes] = useState<SandboxRow[]>([]);
  const [sandboxId, setSandboxId] = useState<string>(ALL_SANDBOXES);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setError(null);
    const scope = sandboxId ? `?sandbox_id=${encodeURIComponent(sandboxId)}` : '';
    try {
      const resp = await adminAPI.fetch(`/api/admin/cash/whereabouts${scope}`);
      if (!resp.ok) throw new Error(`Whereabouts returned ${resp.status}`);
      setData(await resp.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [sandboxId]);

  // Sandbox list once on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await adminAPI.fetch('/api/admin/sandboxes');
        if (!resp.ok || cancelled) return;
        const d = await resp.json();
        setSandboxes(d.sandboxes || []);
      } catch {
        /* best-effort */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Default to the admin's own sandbox once the list + auth resolve.
  useEffect(() => {
    if (userChoseRef.current || sandboxes.length === 0) return;
    const own = user ? sandboxes.find((s) => s.owner_id === user.id) : undefined;
    const target = own?.sandbox_id ?? sandboxes[0].sandbox_id;
    setSandboxId((prev) => (prev === target ? prev : target));
  }, [sandboxes, user]);

  // Poll while mounted.
  useEffect(() => {
    void fetchData();
    const id = setInterval(() => void fetchData(), REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchData]);

  const people = data?.people ?? [];
  const stuck = people.filter((p) => (p.stuck ?? []).length > 0);
  const watchCount = people.filter((p) => (p.watch ?? []).length > 0).length;
  const showSandbox = sandboxId === ALL_SANDBOXES;

  return (
    <div className={embedded ? 'cwp cwp--embedded' : 'cwp'}>
      <div className="cwp-toolbar">
        <div className="cwp-summary">
          <span className="cwp-summary__total">{data?.total ?? 0} tracked</span>
          <span
            className={
              (data?.stuck_count ?? 0) > 0
                ? 'cwp-summary__stuck cwp-summary__stuck--alert'
                : 'cwp-summary__stuck'
            }
          >
            {data?.stuck_count ?? 0} stuck
          </span>
          {watchCount > 0 && <span className="cwp-summary__watch">{watchCount} watch</span>}
        </div>
        <div className="cwp-controls">
          <select
            className="cwp-select"
            value={sandboxId}
            onChange={(e) => {
              userChoseRef.current = true;
              setSandboxId(e.target.value);
            }}
            aria-label="Sandbox scope"
          >
            <option value={ALL_SANDBOXES}>All sandboxes</option>
            {sandboxes.map((s) => (
              <option key={s.sandbox_id} value={s.sandbox_id}>
                {s.name} ({s.owner_id})
              </option>
            ))}
          </select>
          <button type="button" className="cwp-refresh" onClick={() => void fetchData()}>
            Refresh
          </button>
        </div>
      </div>

      {error && <div className="cwp-error">{error}</div>}
      {loading && !data && <div className="cwp-loading">Loading…</div>}

      {stuck.length > 0 && (
        <section className="cwp-section cwp-section--stuck">
          <h3 className="cwp-section__title">⚠ Stuck ({stuck.length})</h3>
          <p className="cwp-section__hint">
            Invariant violations the world loop should never leave standing — investigate before
            they snowball.
          </p>
          <div className="cwp-table-wrap">
            <table className="cwp-table">
              <thead>
                <tr>
                  <th>Persona / flags</th>
                  <th>Status</th>
                  <th>Where / what</th>
                  <th>Time</th>
                  <th>Energy</th>
                  <th className="cwp-num">Bankroll</th>
                  <th className="cwp-num">vs you</th>
                  {showSandbox && <th>Sandbox</th>}
                </tr>
              </thead>
              <tbody>
                {stuck.map((p) => (
                  <PersonRow
                    key={`${p.sandbox_id ?? ''}:${p.personality_id}`}
                    person={p}
                    showSandbox={showSandbox}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="cwp-section">
        <h3 className="cwp-section__title">Everyone off the felt</h3>
        {people.length === 0 && !loading ? (
          <p className="cwp-empty">No one is seated, idle, or off-grid in this scope.</p>
        ) : (
          <div className="cwp-table-wrap">
            <table className="cwp-table">
              <thead>
                <tr>
                  <th>Persona</th>
                  <th>Status</th>
                  <th>Where / what</th>
                  <th>Time</th>
                  <th>Energy</th>
                  <th className="cwp-num">Bankroll</th>
                  <th className="cwp-num">vs you</th>
                  {showSandbox && <th>Sandbox</th>}
                </tr>
              </thead>
              <tbody>
                {people.map((p) => (
                  <PersonRow
                    key={`${p.sandbox_id ?? ''}:${p.personality_id}`}
                    person={p}
                    showSandbox={showSandbox}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export default CashWhereaboutsPanel;
