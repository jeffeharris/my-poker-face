/**
 * ActivityTicker — recent AI movement events in the lobby.
 *
 * Read-only surface that makes the world feel alive. Renders the
 * most recent events from the in-memory ring buffer
 * (`cash_mode/activity.py`). The events themselves are populated
 * server-side by the realtime ticker / `refresh_unseated_tables` — see
 * Lobby.tsx for the socket + poll plumbing that feeds this list.
 *
 * Motion: new rows slide+fade in at the top and push the rest down
 * (framer `layout`). When a *burst* lands in one render (initial load /
 * reconnect catch-up) each new row gets a small jittered entrance delay
 * so it cascades in instead of clumping; a lone steady-state insert gets
 * ~0 delay. Honors `prefers-reduced-motion` (instant, no layout).
 *
 * Renders nothing meaningful when `events` is empty (shows a waiting
 * line). The ticker should never claim activity that hasn't happened.
 */

import { useLayoutEffect, useMemo, useRef, type ReactNode } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import {
  HandCoins,
  Gift,
  ReceiptText,
  Sparkles,
  DoorOpen,
  Briefcase,
  Flame,
  Gauge,
} from 'lucide-react';
import type { LobbyEvent, WorldPace } from './types';
import './CashMode.css';

interface ActivityTickerProps {
  events: LobbyEvent[];
  /** Current world pace; omit (or null) to hide the speed control. */
  worldPace?: WorldPace | null;
  /** Pace setter — required for the speed control to render. */
  onPaceChange?: (pace: WorldPace) => void;
}

/** Stable identity for a feed row — drives de-duping (Lobby's merge) AND
 *  the entrance-animation key, so an already-shown row never re-animates.
 *  The player's own last-stand line is re-synthesized with a fresh
 *  timestamp on every poll, so all of its copies collapse onto one key
 *  (otherwise the standing self-warning would re-flash every poll). */
export function feedEventKey(e: LobbyEvent): string {
  if (e.type === 'last_stand' && e.reason === 'self') return 'self:last_stand';
  return `${e.created_at}|${e.type}|${e.personality_id}`;
}

/** Pace presented as a fast-forward speed control. The real tick
 *  intervals (subtle ≈40s, lively ≈5s, bustling ≈2.2s) aren't clean
 *  multiples, so chevrons convey slow→fast honestly without claiming a
 *  literal "2×/4×". Names live in the tooltip + aria-label. */
const PACE_OPTIONS: { value: WorldPace; chevrons: string; label: string }[] = [
  { value: 'subtle', chevrons: '›', label: 'Subtle' },
  { value: 'lively', chevrons: '››', label: 'Lively' },
  { value: 'bustling', chevrons: '›››', label: 'Bustling' },
];

// Burst cascade: each new row in a same-render batch is delayed
// STEP × its position (+ jitter), capped so a 30-row reconnect dump
// doesn't take seconds — only the first few rows are on-screen anyway
// (the list is a fixed ~5-row scroll window).
const CASCADE_STEP_MS = 70;
const CASCADE_JITTER_MS = 40;
const CASCADE_CAP = 6;
const ROW_DURATION_S = 0.28;
const ROW_EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];

/** Drop `big_loss` events that are the mirror of a `big_win` already in
 *  the list — same hand, same chip movement, just framed from the loser's
 *  POV. The backend emits both halves so per-personality filters can pick
 *  either side, but the ticker should read as one event per chip exchange.
 *  Orphaned losses (no matching win in the window) still render so we
 *  don't silently lose activity. */
function dedupeChipPairs(events: LobbyEvent[]): LobbyEvent[] {
  const winKeys = new Set<string>();
  for (const e of events) {
    if (e.type === 'big_win') {
      winKeys.add(`${e.created_at}|${e.personality_id}|${e.reason}`);
    }
  }
  return events.filter((e) => {
    if (e.type !== 'big_loss') return true;
    const mirrored = `${e.created_at}|${e.reason}|${e.personality_id}`;
    return !winKeys.has(mirrored);
  });
}

/** Per-type leading glyph. Chip movement / staking gets the gold coin;
 *  vice/hustle their own marks; everything else a neutral dot. */
function renderEventIcon(type: LobbyEvent['type']): ReactNode {
  const iconProps = {
    size: 14,
    className: 'lobby-ticker__icon',
    'aria-hidden': true,
  } as const;
  switch (type) {
    case 'big_win':
    case 'ai_stake':
    case 'ai_payoff':
      return <HandCoins {...iconProps} />;
    case 'ai_forgiven':
      return <Gift {...iconProps} />;
    case 'ai_default':
      return <ReceiptText {...iconProps} />;
    case 'vice_start':
      return <Sparkles {...iconProps} />;
    case 'vice_end':
    case 'hustle_end':
      return <DoorOpen {...iconProps} />;
    case 'hustle_start':
      return <Briefcase {...iconProps} />;
    case 'last_stand':
      return <Flame {...iconProps} />;
    default:
      return <span className="lobby-ticker__dot" aria-hidden="true" />;
  }
}

export function ActivityTicker({ events, worldPace = null, onPaceChange }: ActivityTickerProps) {
  const visibleEvents = useMemo(() => dedupeChipPairs(events), [events]);
  const showPace = worldPace != null && typeof onPaceChange === 'function';
  const prefersReduced = useReducedMotion();

  // Assign a cascade delay to each key the first time it appears. Reading
  // the cached value keeps already-mounted rows stable (the delay only
  // matters at mount), and counting newcomers per render gives a single
  // insert ~0 while a burst staggers. Pruning keeps the map bounded.
  const delayByKey = useRef<Map<string, number>>(new Map());
  const delays = delayByKey.current;
  let newcomerIdx = 0;
  for (const e of visibleEvents) {
    const k = feedEventKey(e);
    if (!delays.has(k)) {
      const step = Math.min(newcomerIdx, CASCADE_CAP);
      delays.set(k, step * CASCADE_STEP_MS + Math.random() * CASCADE_JITTER_MS);
      newcomerIdx += 1;
    }
  }
  if (delays.size > 120) {
    const live = new Set(visibleEvents.map(feedEventKey));
    for (const k of [...delays.keys()]) if (!live.has(k)) delays.delete(k);
  }

  // Scroll anchoring: when the user has scrolled back into history, keep
  // their view pinned to the same content as new rows prepend (compensate
  // scrollTop by the added height). At the top, ride along so the freshest
  // event stays visible. framer's `layout` fakes motion with transforms,
  // so scrollHeight already reflects the final layout here.
  const listRef = useRef<HTMLUListElement>(null);
  const prevScrollHeight = useRef(0);
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const atTop = el.scrollTop <= 4;
    const grew = el.scrollHeight - prevScrollHeight.current;
    if (!atTop && grew > 0) el.scrollTop += grew;
    prevScrollHeight.current = el.scrollHeight;
  }, [visibleEvents]);

  return (
    <div className="lobby-ticker" aria-label="Recent table activity">
      <div className="lobby-ticker__header">
        <h3 className="lobby-ticker__heading">Activity</h3>
        {showPace && (
          <div
            className="lobby-ticker__pace"
            role="group"
            aria-label="World pace — how fast the other tables play"
          >
            <Gauge size={19} className="lobby-ticker__pace-icon" aria-hidden="true" />
            {PACE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`lobby-ticker__pace-btn${worldPace === opt.value ? ' is-active' : ''}`}
                onClick={() => onPaceChange?.(opt.value)}
                aria-pressed={worldPace === opt.value}
                aria-label={`World pace: ${opt.label}`}
                title={`${opt.label} — how fast the other tables play while you're here`}
              >
                {opt.chevrons}
              </button>
            ))}
          </div>
        )}
      </div>
      <ul className="lobby-ticker__list" ref={listRef}>
        {visibleEvents.length === 0 && (
          <li className="lobby-ticker__empty">Waiting for the next move…</li>
        )}
        {visibleEvents.map((event) => {
          const key = feedEventKey(event);
          const className = `lobby-ticker__item lobby-ticker__item--${event.type}`;
          const content = (
            <>
              {renderEventIcon(event.type)}
              <span className="lobby-ticker__message" title={event.message}>
                {event.message}
              </span>
            </>
          );
          // Reduced motion: plain row, no entrance, no layout shifting.
          if (prefersReduced) {
            return (
              <li key={key} className={className}>
                {content}
              </li>
            );
          }
          return (
            <motion.li
              key={key}
              layout
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{
                duration: ROW_DURATION_S,
                ease: ROW_EASE,
                delay: (delays.get(key) ?? 0) / 1000,
              }}
              className={className}
            >
              {content}
            </motion.li>
          );
        })}
      </ul>
    </div>
  );
}
