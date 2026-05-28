/**
 * interhandTicker — curate world-ticker events for the between-hand screen.
 *
 * In cash/career mode the shuffle transition becomes a "meanwhile,
 * elsewhere" world ticker (a sports-ticker for the rest of the room)
 * instead of a "Next Hand #N" badge. The realtime world keeps churning
 * while you're seated, so this picks the handful of bigger / rarer beats
 * that happened since the current hand started.
 *
 * Pure selection only — no React, no rendering — so it's trivially
 * unit-testable. The caller maps the result to display rows.
 */

import type { LobbyEvent } from './types';
import { dedupeFeed, feedEventKey } from './tickerEvents';

/** Pure "comings and goings" — an AI sitting down or getting up. Suppressed
 *  from the interhand ticker by design: the between-hand screen is for the
 *  bigger, rarer beats, not routine churn. */
const SUPPRESSED_TYPES: ReadonlySet<string> = new Set(['join', 'leave']);

/** Display priority — lower shows first. Ranks rarer / higher-drama beats
 *  above routine ones so a capped digest keeps the good stuff. Unlisted
 *  types fall to DEFAULT_PRIORITY (still shown, just after the ranked
 *  ones) so a new backend event type never silently vanishes. */
const PRIORITY: Record<string, number> = {
  whale_arrival: 0,
  last_stand: 1,
  ai_default: 2,
  ai_forgiven: 3,
  ai_requests_forgiveness: 3,
  bust: 4,
  big_win: 5,
  big_loss: 5,
  ai_stake: 6,
  ai_payoff: 6,
  burst_summary: 7,
  whale_departure: 8,
  all_in: 9,
  vice_start: 10,
  hustle_start: 10,
  vice_end: 11,
  hustle_end: 11,
};
const DEFAULT_PRIORITY = 7;

/**
 * Pick the events to show on the interhand world ticker.
 *
 * Drops comings/goings, collapses the big_win/big_loss mirror pair to a
 * single line, de-dupes by stable key, then orders by drama priority (most
 * recent first within a tier) and keeps the top `max`.
 */
export function selectInterhandTicker(events: LobbyEvent[], max: number): LobbyEvent[] {
  const seen = new Set<string>();
  const candidates = dedupeFeed(events).filter((e) => {
    if (SUPPRESSED_TYPES.has(e.type)) return false;
    const key = feedEventKey(e);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  candidates.sort((a, b) => {
    const pa = PRIORITY[a.type] ?? DEFAULT_PRIORITY;
    const pb = PRIORITY[b.type] ?? DEFAULT_PRIORITY;
    if (pa !== pb) return pa - pb;
    // Newer first within a priority tier.
    return a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0;
  });

  return candidates.slice(0, Math.max(0, max));
}
