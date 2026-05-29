/**
 * Run-out reveal director types — the per-card reaction schedule the backend
 * emits once (event `runout_schedule`) at the all-in hole-card reveal, and which
 * `useRunoutDirector` walks to drive per-card avatar reactions on a client-owned
 * beat. See docs/plans/RUNOUT_REVEAL_DIRECTOR.md.
 *
 * The payload carries reactions + per-card timing only — never any board card —
 * so future-street cards can't reach the client ahead of their reveal.
 */

/** One AI face triggered by a single revealed card's equity delta. */
export interface RunoutReaction {
  player_name: string;
  emotion: string;
}

/**
 * One reveal step on the timeline. Finer-grained than a street: the flop is
 * three steps (`card_index` 0/1/2). `phase` is INITIAL | FLOP | TURN | RIVER |
 * SHOWDOWN; INITIAL is the matchup beat at hole-card reveal, SHOWDOWN the final
 * lock-up. A step may carry zero reactions (a card that moved nobody's equity).
 */
export interface RunoutStep {
  phase: string;
  card_index: number;
  reactions: RunoutReaction[];
}

export interface RunoutSchedule {
  steps: RunoutStep[];
}
