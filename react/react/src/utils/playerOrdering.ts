import type { Player } from '../types';

/**
 * Order the AI opponents the way they sit at the table *relative to the human*
 * — by clockwise turn-order distance from the human's seat (the player to the
 * human's immediate left first, wrapping around).
 *
 * This is the single source of truth for "opponent order around the felt,"
 * shared by the mobile table layout and the quick-chat target picker so the
 * avatars line up in the same order in both places.
 *
 * The human is dropped from the result. If there's no human in the list
 * (spectator / all-AI views) the non-human players are returned in their
 * original order.
 */
export function orderOpponentsRelativeToHuman(players: Player[]): Player[] {
  const humanIndex = players.findIndex((p) => p.is_human);
  if (humanIndex < 0) return players.filter((p) => !p.is_human);

  const total = players.length;
  return players
    .map((player, index) => ({ player, index }))
    .filter(({ player }) => !player.is_human)
    .sort(
      (a, b) => ((a.index - humanIndex + total) % total) - ((b.index - humanIndex + total) % total)
    )
    .map(({ player }) => player);
}
