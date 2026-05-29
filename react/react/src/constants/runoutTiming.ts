/**
 * runoutTiming — single source of truth for the mobile all-in run-out reveal
 * director's beat. Sibling to `interhandTiming.ts`.
 *
 * Under the run-out design (option B: one client-agnostic backend path), the
 * backend still paces the board street-by-street; the director owns the *finer*
 * timing — when each card's avatar reaction fires — re-timing reactions from
 * street granularity (Phase 1) to per-card. Reaction offsets are aligned to the
 * community-card slide cascade (`useCommunityCardAnimation`: flop cards stagger
 * 1.0s apart, ~0.825s slide each) so a face changes as its card settles.
 *
 * All values are milliseconds.
 */
export const RUNOUT_TIMING = {
  /** After the hole-card reveal cascade: when the matchup-read (INITIAL) faces fire. */
  initialReactionDelayMs: 1200,
  /** Gap between consecutive flop cards — must match the community-card cascade (1.0s/card). */
  perCardStaggerMs: 1000,
  /** Within a single card's slide-in, how long until its reaction fires (so the
   *  face lands as the card settles rather than the instant it appears). */
  reactionAfterCardMs: 600,
  /** After the river card settles: when the SHOWDOWN lock-up faces fire. */
  showdownReactionDelayMs: 900,
  /** Fast-forward compresses every beat to ~10%, mirroring the backend `_ff_aware_sleep`. */
  ffMultiplier: 0.1,
  /** Safety cap: the director releases reaction ownership after this even if the
   *  board stalls (a deleted/abandoned game), so backend reactions are never
   *  suppressed forever. */
  safetyCapMs: 15000,
} as const;
