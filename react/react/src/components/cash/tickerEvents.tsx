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
  Coins,
  Gift,
  ReceiptText,
  Sparkles,
  DoorOpen,
  Briefcase,
  Flame,
  TrendingUp,
  TrendingDown,
  Zap,
  Skull,
  HandHeart,
  Gem,
  Ellipsis,
  Trophy,
} from 'lucide-react';
import type { LobbyEvent } from './types';

/** Stable identity for a feed row — drives de-duping (Lobby's merge) AND
 *  the entrance-animation key, so an already-shown row never re-animates.
 *  The player's own last-stand line is re-synthesized with a fresh
 *  timestamp on every poll, so all of its copies collapse onto one key
 *  (otherwise the standing self-warning would re-flash every poll). */
export function feedEventKey(e: LobbyEvent): string {
  if (e.type === 'last_stand' && e.reason === 'self') return 'self:last_stand';
  // Discriminate the composed `primary` summary from its `primary:false`
  // atomic siblings (same hand → identical created_at/type/personality_id)
  // so the rolling-feed merge keeps both rather than collapsing them onto
  // one key and dropping the row the ticker actually wants to show.
  const tier = e.primary === false ? 's' : 'p';
  return `${e.created_at}|${e.type}|${e.personality_id}|${tier}`;
}

/** Reduce the raw event buffer to the rows the ticker should display.
 *
 *  Two passes:
 *  1. Drop `primary: false` events — the per-hand atomic win/all-in/bust
 *     beats that the single-hand path demotes in favor of one composed
 *     summary line. They ride the wire for per-AI filtering but never
 *     render. (Absent `primary` ⇒ shown, so other event types are
 *     unaffected.)
 *  2. Drop `big_loss` events that mirror a `big_win` already present —
 *     same hand, same chip movement, just the loser's POV. The backend
 *     emits both halves so per-personality filters can pick either side
 *     (relevant on the burst path, which still emits atomic pairs), but the
 *     ticker reads as one event per chip exchange. Orphaned losses (no
 *     matching win in the window) still render so activity isn't lost. */
export function dedupeFeed(events: LobbyEvent[]): LobbyEvent[] {
  // A busted player is also removed from the table on the next refresh tick
  // as a `forced_leave`, which just restates "busted out". Collapse it: if a
  // bust for that personality is anywhere in the window (including the hidden
  // primary:false copy, so we still catch it when the bust was folded into a
  // composed summary line), drop their forced-leave row. Other leave reasons
  // (take_break / bored_move / stake_up_queued) are real movement and kept.
  // If the bust has already aged out of the window, the leave still shows so
  // we don't silently lose the signal.
  const bustedPids = new Set<string>();
  for (const e of events) {
    if (e.type === 'bust') bustedPids.add(e.personality_id);
  }

  const visible = events.filter((e) => {
    if (e.primary === false) return false;
    if (e.type === 'leave' && e.reason === 'forced_leave' && bustedPids.has(e.personality_id)) {
      return false;
    }
    return true;
  });
  const winKeys = new Set<string>();
  for (const e of visible) {
    if (e.type === 'big_win') {
      winKeys.add(`${e.created_at}|${e.personality_id}|${e.reason}`);
    }
  }
  return visible.filter((e) => {
    if (e.type !== 'big_loss') return true;
    const mirrored = `${e.created_at}|${e.reason}|${e.personality_id}`;
    return !winKeys.has(mirrored);
  });
}

/** Per-type leading glyph. Chip drama (win/loss/all-in/bust) and the AI
 *  economy each get a distinct mark; off-grid (vice/hustle) and whales get
 *  their own; low-signal room movement (join/leave) and the quiet whale
 *  recall fall through to a neutral dot. Colours live in CashMode.css. */
export function renderEventIcon(type: LobbyEvent['type']): ReactNode {
  const iconProps = {
    size: 14,
    className: 'lobby-ticker__icon',
    'aria-hidden': true,
  } as const;
  switch (type) {
    // Chip drama
    case 'big_win':
      return <TrendingUp {...iconProps} />;
    case 'big_loss':
      return <TrendingDown {...iconProps} />;
    case 'all_in':
      return <Zap {...iconProps} />;
    case 'bust':
      return <Skull {...iconProps} />;
    case 'last_stand':
      return <Flame {...iconProps} />;
    // AI economy
    case 'ai_stake':
      return <HandCoins {...iconProps} />;
    case 'ai_payoff':
      return <Coins {...iconProps} />;
    case 'ai_default':
      return <ReceiptText {...iconProps} />;
    case 'ai_forgiven':
      return <Gift {...iconProps} />;
    case 'ai_requests_forgiveness':
      return <HandHeart {...iconProps} />;
    // Off-grid
    case 'vice_start':
      return <Sparkles {...iconProps} />;
    case 'vice_end':
    case 'hustle_end':
      return <DoorOpen {...iconProps} />;
    case 'hustle_start':
      return <Briefcase {...iconProps} />;
    // Whales — arrival is the pull signal; departure is a quiet dot.
    case 'whale_arrival':
      return <Gem {...iconProps} />;
    // Circuit Main Event lifecycle beats (P3.7).
    case 'tournament_milestone':
    case 'tournament_bubble':
    case 'tournament_winner':
      return <Trophy {...iconProps} />;
    // Meta — aggregate catch-up summary.
    case 'burst_summary':
      return <Ellipsis {...iconProps} />;
    // join, leave, whale_departure → recessive dot.
    default:
      return <span className="lobby-ticker__dot" aria-hidden="true" />;
  }
}
