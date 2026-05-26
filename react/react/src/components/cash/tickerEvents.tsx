/**
 * tickerEvents — shared helpers for rendering world/lobby activity events.
 *
 * Extracted from ActivityTicker so both the lobby feed and the in-game
 * interhand "meanwhile, elsewhere" ticker can reuse the same de-dup, keying,
 * and per-type glyphs without importing from a component module (which would
 * defeat React Fast Refresh).
 */

import type { ReactNode } from 'react';
import {
  HandCoins,
  Gift,
  ReceiptText,
  Sparkles,
  DoorOpen,
  Briefcase,
  Flame,
} from 'lucide-react';
import type { LobbyEvent } from './types';

/** Stable identity for a feed row — drives de-duping (Lobby's merge) AND
 *  the entrance-animation key, so an already-shown row never re-animates.
 *  The player's own last-stand line is re-synthesized with a fresh
 *  timestamp on every poll, so all of its copies collapse onto one key
 *  (otherwise the standing self-warning would re-flash every poll). */
export function feedEventKey(e: LobbyEvent): string {
  if (e.type === 'last_stand' && e.reason === 'self') return 'self:last_stand';
  return `${e.created_at}|${e.type}|${e.personality_id}`;
}

/** Drop `big_loss` events that are the mirror of a `big_win` already in
 *  the list — same hand, same chip movement, just framed from the loser's
 *  POV. The backend emits both halves so per-personality filters can pick
 *  either side, but the ticker should read as one event per chip exchange.
 *  Orphaned losses (no matching win in the window) still render so we
 *  don't silently lose activity. */
export function dedupeChipPairs(events: LobbyEvent[]): LobbyEvent[] {
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
export function renderEventIcon(type: LobbyEvent['type']): ReactNode {
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
