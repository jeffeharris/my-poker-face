/**
 * WhereaboutsDrawer — "where'd everyone go?"
 *
 * A power-user companion to the lobby: shows where the AIs you've met
 * are right now when they're not at a table you can see — recharging in
 * the idle pool, off earning on a side hustle, indulging a vice, or
 * seated at another table. Scoped server-side to opponents you've
 * actually played (`/api/cash/whereabouts` filters to "met"), so it
 * reads as your own little black book rather than a roster dump.
 *
 * Centered modal, portal-to-body (mirrors NetWorthDrawer so it clears
 * the fixed PageLayout header). Refetches when `refreshTick` changes so
 * it stays in lockstep with the lobby's own poll/socket refresh.
 */

import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  X,
  MapPin,
  Coffee,
  Briefcase,
  Dice5,
  Armchair,
  ArrowUpRight,
  EyeOff,
  ChevronDown,
} from 'lucide-react';
import { getWhereabouts } from './api';
import { absolutizeAvatarUrl } from './avatarUrl';
import type { IdleReason, WhereaboutsPerson, WhereaboutsStatus } from './types';
import { logger } from '../../utils/logger';
import './WhereaboutsDrawer.css';

interface WhereaboutsDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  /** Bumped by the parent on every lobby refresh so this stays in sync. */
  refreshTick?: number;
}

const REASON_LABEL: Record<IdleReason, string> = {
  forced_leave: 'Busted — rebuilding',
  stake_up_queued: 'Looking to move up',
  take_break: 'Taking a breather',
  bored_move: 'Between tables',
};

/** Section order + presentation for each status group. Off-table states
 *  lead (that's the point of the view); seated trails. */
const SECTIONS: {
  status: WhereaboutsStatus;
  label: string;
  icon: React.ReactNode;
}[] = [
  { status: 'idle', label: 'Recharging', icon: <Coffee size={15} aria-hidden="true" /> },
  {
    status: 'side_hustle',
    label: 'On a side hustle',
    icon: <Briefcase size={15} aria-hidden="true" />,
  },
  { status: 'vice', label: 'Out indulging', icon: <Dice5 size={15} aria-hidden="true" /> },
  { status: 'seated', label: 'At another table', icon: <Armchair size={15} aria-hidden="true" /> },
];

/** Compact, human-readable duration. Clamps negatives to 0 — callers
 *  decide the "overdue" wording separately. */
function fmtDuration(seconds: number | null): string {
  if (seconds == null) return '';
  const s = Math.max(0, Math.abs(seconds));
  if (s >= 3600) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
  if (s >= 60) return `${Math.floor(s / 60)}m`;
  return `${s}s`;
}

/** The one-line "what are they up to" detail under the name. */
function detailLine(p: WhereaboutsPerson): string {
  switch (p.status) {
    case 'idle': {
      const base =
        p.reason === 'stake_up_queued' && p.target_stake
          ? `Looking for a ${p.target_stake} seat`
          : p.reason
            ? REASON_LABEL[p.reason]
            : 'Idle';
      // Recharge is fraction-toward-baseline — the idle pool *is* "Recharging",
      // so show how rested they are (100% = fully recharged, ~ready to return).
      if (p.recharge != null) {
        return `${base} · ${Math.round(p.recharge * 100)}% charged`;
      }
      const since = fmtDuration(p.seconds_in_state);
      return since ? `${base} · ${since}` : base;
    }
    case 'side_hustle':
    case 'vice':
      return p.narration || (p.status === 'vice' ? 'Off blowing chips' : 'Off earning');
    case 'seated':
      return p.table_name
        ? `${p.table_name} (${p.stake_label})`
        : `${p.stake_label ?? ''} table`.trim();
    default:
      return '';
  }
}

/** Right-aligned timing badge: returns-in for hustle/vice, idle-for
 *  otherwise. Hustle/vice past their end read "back any moment". */
function timingBadge(p: WhereaboutsPerson): string | null {
  if (p.status === 'side_hustle' || p.status === 'vice') {
    if (p.seconds_remaining == null) return null;
    if (p.seconds_remaining <= 0) return 'back any moment';
    return `back in ${fmtDuration(p.seconds_remaining)}`;
  }
  if (p.status === 'idle' && p.seconds_in_state != null) {
    return `${fmtDuration(p.seconds_in_state)} away`;
  }
  return null;
}

/** A short "what recently happened to them" memory from the newest event in
 *  the AI's ring buffer. null when there's no recent drama. */
function recentMemory(p: WhereaboutsPerson): string | null {
  const ev = p.recent?.length ? p.recent[p.recent.length - 1] : null;
  if (!ev) return null;
  const vs = ev.opponent ? ` ${ev.opponent}` : '';
  switch (ev.type) {
    case 'bust':
      return '💥 just busted out';
    case 'suckout':
      return `🎣 sucked out${vs ? ` on${vs}` : ''}`;
    case 'all_in':
      return `🃏 went all-in${vs ? ` vs${vs}` : ''}`;
    case 'nice_pot':
      return '💰 scooped a big pot';
    default:
      return null;
  }
}

/** PnL chip — the player's lifetime result vs this opponent. The muted
 *  "vs. you" suffix is what makes the number self-describing in-row, so a
 *  lay user reads "+$420 vs. you" as "you're up $420 against this person"
 *  without needing a legend. */
function PnlChip({ netPnl, hands }: { netPnl: number; hands: number }) {
  if (!hands) return null;
  const suffix = <span className="whereabouts-row__pnl-label"> vs. you</span>;
  if (netPnl === 0) {
    return <span className="whereabouts-row__pnl whereabouts-row__pnl--even">even{suffix}</span>;
  }
  const up = netPnl > 0;
  return (
    <span
      className={`whereabouts-row__pnl ${up ? 'whereabouts-row__pnl--up' : 'whereabouts-row__pnl--down'}`}
      title={`You're ${up ? 'up' : 'down'} $${Math.abs(netPnl).toLocaleString()} against them over ${hands} hand${hands === 1 ? '' : 's'}`}
    >
      {up ? '+' : '−'}${Math.abs(netPnl).toLocaleString()}
      {suffix}
    </span>
  );
}

function PersonRow({ person }: { person: WhereaboutsPerson }) {
  const [expanded, setExpanded] = useState(false);
  const badge = timingBadge(person);
  const memory = recentMemory(person);
  // Backend returns a relative avatar path ("/api/avatar/..."); absolutize
  // it (same as TableCard) or it 404s to the SPA fallback in dev.
  const avatarSrc = absolutizeAvatarUrl(person.avatar_url ?? null);
  // Side-hustle / vice narrations can run long; let the player tap the
  // row to drop the 2-line clamp and read the whole thing. Idle/seated
  // details are short, so they're not expandable.
  const expandable =
    (person.status === 'side_hustle' || person.status === 'vice') && !!person.narration;
  const toggle = () => expandable && setExpanded((v) => !v);
  return (
    <li
      className={`whereabouts-row${expandable ? ' whereabouts-row--expandable' : ''}${expanded ? ' is-expanded' : ''}`}
      onClick={toggle}
      role={expandable ? 'button' : undefined}
      tabIndex={expandable ? 0 : undefined}
      aria-expanded={expandable ? expanded : undefined}
      onKeyDown={
        expandable
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggle();
              }
            }
          : undefined
      }
    >
      <div className="whereabouts-row__avatar" aria-hidden="true">
        {avatarSrc ? (
          <img src={avatarSrc} alt="" loading="lazy" />
        ) : (
          <span className="whereabouts-row__avatar-fallback">
            {(person.name || '?').charAt(0).toUpperCase()}
          </span>
        )}
      </div>
      <div className="whereabouts-row__body">
        <div className="whereabouts-row__name-line">
          <span className="whereabouts-row__name">{person.name}</span>
          <PnlChip netPnl={person.net_pnl} hands={person.hands_played} />
        </div>
        <div className={`whereabouts-row__detail${expanded ? ' is-expanded' : ''}`}>
          {detailLine(person)}
        </div>
        {memory && <div className="whereabouts-row__memory">{memory}</div>}
      </div>
      {badge && <span className="whereabouts-row__timing">{badge}</span>}
      {expandable && (
        <ChevronDown size={14} className="whereabouts-row__chevron" aria-hidden="true" />
      )}
    </li>
  );
}

/** How many rows a section shows before collapsing behind "Show all". */
const SECTION_PREVIEW = 5;

/** One status group. Holds its own expand state so a long list (e.g. a
 *  dozen idlers, or many side hustles whose narrations you want to read)
 *  starts compact and expands on demand. */
function DrawerSection({
  label,
  icon,
  rows,
}: {
  label: string;
  icon: React.ReactNode;
  rows: WhereaboutsPerson[];
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? rows : rows.slice(0, SECTION_PREVIEW);
  const overflow = rows.length - SECTION_PREVIEW;
  return (
    <section className="whereabouts-drawer__section">
      <h3 className="whereabouts-drawer__section-title">
        {icon}
        <span>{label}</span>
        <span className="whereabouts-drawer__section-count">{rows.length}</span>
      </h3>
      {rows.length === 0 ? (
        <p className="whereabouts-drawer__section-empty">Nobody right now.</p>
      ) : (
        <>
          <ul className="whereabouts-drawer__list">
            {visible.map((p) => (
              <PersonRow key={person_key(p)} person={p} />
            ))}
          </ul>
          {overflow > 0 && (
            <button
              type="button"
              className={`whereabouts-drawer__section-toggle${expanded ? ' is-expanded' : ''}`}
              onClick={() => setExpanded((v) => !v)}
            >
              <ChevronDown size={14} aria-hidden="true" />
              {expanded ? 'Show fewer' : `Show all ${rows.length}`}
            </button>
          )}
        </>
      )}
    </section>
  );
}

export function WhereaboutsDrawer({ isOpen, onClose, refreshTick = 0 }: WhereaboutsDrawerProps) {
  const [people, setPeople] = useState<WhereaboutsPerson[] | null>(null);
  const [unmetCount, setUnmetCount] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getWhereabouts();
      setPeople(data.people);
      setUnmetCount(data.unmet_count ?? 0);
      setLoadError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error('Failed to load whereabouts:', msg);
      setLoadError(msg);
    }
  }, []);

  // Fetch on open and whenever the lobby refreshes (refreshTick). Skip
  // work entirely while closed so it doesn't poll in the background.
  useEffect(() => {
    if (!isOpen) return;
    void load();
  }, [isOpen, refreshTick, load]);

  if (!isOpen) return null;

  // Keep ALL sections (even empty ones) so the player can see the
  // structure — "On a side hustle: nobody right now" is informative.
  const grouped = SECTIONS.map((section) => ({
    ...section,
    rows: (people ?? []).filter((p) => p.status === section.status),
  }));

  const isEmpty = people !== null && people.length === 0;

  return createPortal(
    <div className="whereabouts-drawer__overlay" onClick={onClose}>
      <div
        className="whereabouts-drawer__sheet"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Whereabouts"
      >
        <div className="whereabouts-drawer__header">
          <h2 className="whereabouts-drawer__title">
            <MapPin size={18} aria-hidden="true" />
            <span>Who's around</span>
          </h2>
          <button
            type="button"
            className="whereabouts-drawer__close"
            onClick={onClose}
            aria-label="Close whereabouts"
          >
            <X size={20} />
          </button>
        </div>

        <div className="whereabouts-drawer__body">
          {loadError && (
            <div className="whereabouts-drawer__error" role="alert">
              {loadError}
            </div>
          )}
          {people === null && !loadError && (
            <div className="whereabouts-drawer__loading">Loading…</div>
          )}
          {isEmpty && (
            <div className="whereabouts-drawer__empty">
              <p>Nobody you've played is off doing their own thing right now.</p>
              <p className="whereabouts-drawer__empty-hint">
                Play a few hands against the regulars — once you've tangled with someone, you'll see
                where they wander off to between sessions.
              </p>
            </div>
          )}
          {!isEmpty &&
            people !== null &&
            grouped.map((section) => (
              <DrawerSection
                key={section.status}
                label={section.label}
                icon={section.icon}
                rows={section.rows}
              />
            ))}

          {/* Fog of war: a count of who's out there you haven't met.
              Non-interactive — no names, nothing to expand. */}
          {people !== null && unmetCount > 0 && (
            <div className="whereabouts-drawer__unmet" aria-label={`${unmetCount} strangers`}>
              <EyeOff size={14} aria-hidden="true" />
              <span>
                {unmetCount} {unmetCount === 1 ? 'other' : 'others'} around you haven't met
              </span>
            </div>
          )}
        </div>

        <p className="whereabouts-drawer__footnote">
          <ArrowUpRight size={12} aria-hidden="true" />
          Only people you've sat with show up here.
        </p>
      </div>
    </div>,
    document.body
  );
}

/** Stable key: pid is unique per snapshot (one person, one place). */
function person_key(p: WhereaboutsPerson): string {
  return p.personality_id;
}

export default WhereaboutsDrawer;
