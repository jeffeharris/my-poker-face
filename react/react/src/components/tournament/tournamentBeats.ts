/**
 * tournamentBeats — pure render helpers for tournament activity beats.
 *
 * Used by the structural toast notifications (useTournamentEvents) to turn a
 * beat into an icon + one-line message. Beats are produced by tournament/beats.py
 * and described in ./types.ts. (The felt ticker and hub activity feed that also
 * consumed these were removed; the backend beats stream + these helpers remain to
 * drive the toasts, and are the foundation if a richer surface returns later.)
 */

import type { TournamentBeat } from './types';

const ordinal = (n: number): string => {
  const v = n % 100;
  const suffix = v >= 11 && v <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] || 'th';
  return `${n}${suffix}`;
};

/** A name for display — the human reads as "You", AI seats by their field id. */
const who = (playerId: string, isHuman: boolean): string => (isHuman ? 'You' : playerId);

/** Structural beats (table breaks, blinds up, bubble, milestones) are the ones
 *  worth interrupting the felt with a toast; routine knockouts are dropped.
 *  Mirrors the cash ticker's "primary" filter. */
export function isStructuralBeat(b: TournamentBeat): boolean {
  return b.type !== 'knockout';
}

/** Leading glyph — emoji to match the toast/ticker voice (kept tiny + neutral). */
export function beatIcon(b: TournamentBeat): string {
  switch (b.type) {
    case 'knockout':
      return b.is_human ? '☠️' : '💥';
    case 'table_break':
      return '🪑';
    case 'bubble':
      return '🫧';
    case 'milestone':
      return b.kind === 'heads_up' ? '🤝' : b.kind === 'final_table' ? '🏁' : '👑';
    case 'level_up':
      return '⏫';
    case 'level_up_next':
      return '🔔';
  }
}

/** One-line text for the toast notification. */
export function beatText(b: TournamentBeat): string {
  switch (b.type) {
    case 'knockout': {
      const name = who(b.player_id, b.is_human);
      const place = ordinal(b.finishing_position);
      if (b.eliminator) return `${b.eliminator} busts ${name} · ${place}`;
      return b.is_human ? `You busted · ${place}` : `${name} out · ${place}`;
    }
    case 'table_break':
      return `Table ${b.table_id} broke`;
    case 'bubble':
      return `Bubble burst · ${b.paid_places} paid`;
    case 'milestone':
      if (b.kind === 'heads_up') return 'Heads up!';
      if (b.kind === 'final_table') return 'Final table';
      return `Down to ${b.remaining}`;
    case 'level_up':
      return `Blinds up · ${b.small_blind.toLocaleString()}/${b.big_blind.toLocaleString()}`;
    case 'level_up_next':
      return `Blinds up next hand · ${b.small_blind.toLocaleString()}/${b.big_blind.toLocaleString()}`;
  }
}
