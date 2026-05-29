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
  /** When the matchup-read (INITIAL) faces fire — timed to land while the human's
   *  cards are held up presenting (the heroPresentUp hold), so it reads as
   *  "you show your hand → they react"; the cards pull back when the run-out deals. */
  initialReactionDelayMs: 700,
  /** Gap between consecutive flop cards — must match the community-card cascade (1.0s/card). */
  perCardStaggerMs: 1000,
  /** Within a single card's slide-in, how long until its reaction fires (so the
   *  face lands as the card settles rather than the instant it appears). */
  reactionAfterCardMs: 600,
  /** After the river card settles: when the SHOWDOWN lock-up faces fire. */
  showdownReactionDelayMs: 900,
  /** How long the showdown face is held (director stays authoritative) before
   *  releasing ownership — so the next full state push, which carries the
   *  cleared-override baseline emotion, can't instantly revert the lock-up face. */
  showdownHoldMs: 2500,
  /** Fast-forward compresses every beat to ~10%, mirroring the backend `_ff_aware_sleep`. */
  ffMultiplier: 0.1,
  /** Safety cap: the director releases reaction ownership after this even if the
   *  board stalls (a deleted/abandoned game), so backend reactions are never
   *  suppressed forever. */
  safetyCapMs: 15000,
  /** Hero card-commit gesture (the human "presents" their hole cards at the all-in
   *  matchup reveal, then pulls them back when the run-out deals). These durations
   *  feed the inline CSS `animation` shorthand in MobilePokerTable's hero cards via
   *  `heroCardAnimation()`. The keyframe *shape* (reach/spread/tilt) lives in the
   *  `heroPresentUp*`/`heroPullDown*` keyframes in MobilePokerTable.css — only the
   *  timing is centralized here. Seconds (CSS units), not milliseconds.
   *  Hand-tuned with the user; preserve the feel before changing. */
  hero: {
    /** Throw-up "present" duration (matches dealCardIn's slide). */
    presentSec: 0.55,
    /** Pull-back-down duration when the run-out starts dealing. */
    retreatSec: 0.5,
    /** Stagger before the right card follows the left (present only). */
    card2StaggerSec: 0.15,
    /** Shared easing — same curve as the deal-in so it reads smooth. */
    easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
  },
} as const;
