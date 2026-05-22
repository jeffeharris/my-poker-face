/**
 * ActivityTicker — recent AI movement events in the lobby.
 *
 * Read-only surface that makes the world feel alive. Renders the
 * most recent ~10 events from the in-memory ring buffer
 * (`cash_mode/activity.py`). The events themselves are populated
 * server-side by `refresh_unseated_tables` whenever any lobby read
 * runs — which means the ticker is also a visible side effect of
 * the player browsing /cash. v1.5 has no background daemon; the
 * polling + read-side refresh combo is what keeps things moving.
 *
 * Renders nothing when `events` is empty (e.g., right after backend
 * restart). The ticker should never claim activity that hasn't
 * happened.
 */

import { useMemo } from 'react';
import { HandCoins, Gift, ReceiptText } from 'lucide-react';
import type { LobbyEvent } from './types';
import './CashMode.css';

interface ActivityTickerProps {
  events: LobbyEvent[];
}

/** Relative time formatter — used because the absolute UTC timestamp
 *  isn't useful in a ticker. `30s ago`, `2m ago`, `1h ago`. Returns
 *  `just now` for < 5s. */
function formatRelativeTime(createdAt: string): string {
  const then = Date.parse(createdAt);
  if (Number.isNaN(then)) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

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

export function ActivityTicker({ events }: ActivityTickerProps) {
  const visibleEvents = useMemo(() => dedupeChipPairs(events), [events]);
  if (visibleEvents.length === 0) return null;

  return (
    <div className="lobby-ticker" aria-label="Recent table activity">
      <h3 className="lobby-ticker__heading">Activity</h3>
      <ul className="lobby-ticker__list">
        {visibleEvents.map((event, idx) => (
          <li
            key={`${event.created_at}-${event.personality_id}-${event.type}-${idx}`}
            className={`lobby-ticker__item lobby-ticker__item--${event.type}`}
          >
            {event.type === 'big_win' ||
            event.type === 'ai_stake' ||
            event.type === 'ai_payoff' ? (
              <HandCoins
                size={14}
                className="lobby-ticker__icon"
                aria-hidden="true"
              />
            ) : event.type === 'ai_forgiven' ? (
              <Gift
                size={14}
                className="lobby-ticker__icon"
                aria-hidden="true"
              />
            ) : event.type === 'ai_default' ? (
              <ReceiptText
                size={14}
                className="lobby-ticker__icon"
                aria-hidden="true"
              />
            ) : (
              <span className="lobby-ticker__dot" aria-hidden="true" />
            )}
            <span className="lobby-ticker__message">{event.message}</span>
            {event.type === 'leave' && event.comment ? (
              <span className="lobby-ticker__comment">
                {' — '}
                {event.comment}
              </span>
            ) : null}
            <span className="lobby-ticker__time">
              {formatRelativeTime(event.created_at)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
