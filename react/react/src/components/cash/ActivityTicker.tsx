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

export function ActivityTicker({ events }: ActivityTickerProps) {
  if (events.length === 0) return null;

  return (
    <div className="lobby-ticker" aria-label="Recent table activity">
      <h3 className="lobby-ticker__heading">Activity</h3>
      <ul className="lobby-ticker__list">
        {events.map((event, idx) => (
          <li
            key={`${event.created_at}-${event.personality_id}-${event.type}-${idx}`}
            className={`lobby-ticker__item lobby-ticker__item--${event.type}`}
          >
            <span className="lobby-ticker__dot" aria-hidden="true" />
            <span className="lobby-ticker__message">{event.message}</span>
            <span className="lobby-ticker__time">
              {formatRelativeTime(event.created_at)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
