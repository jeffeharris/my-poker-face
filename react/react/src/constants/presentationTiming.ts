/**
 * presentationTiming — the single source of truth for the hand-presentation
 * sequencer's clock (`handSequencer.ts`). Replaces the binary fast-forward
 * (`rushing → 0.1×`) and the scattered deal-gate / reaction / reveal constants
 * with one 3-tier clock and one set of base beat durations.
 *
 * See docs/plans/RUNOUT_PRESENTATION_SEQUENCER.md. All values are milliseconds,
 * expressed at the `watchable` (1.0×) tier; `scale()` applies the active tier.
 *
 * (The hero card-commit *gesture* durations stay in runoutTiming.ts `hero` —
 * those feed CSS `animation` shorthand, a different concern from beat pacing.)
 */

/** How fast the hand plays back. Derived from the existing fast-forward flags. */
export type PacingTier = 'watchable' | 'fast' | 'fastest';

/** Per-tier time multiplier. `fastest` collapses beats (flush + recap → result). */
export const TIER_MULTIPLIER: Record<PacingTier, number> = {
  watchable: 1,
  fast: 0.4, // snappy but every beat still visible + ordered
  fastest: 0, // flush: apply final state, brief recap, then the result
} as const;

/**
 * Map the three live fast-forward signals to a tier.
 *  - `always` game-speed or an all-no-LLM table (`aiInstant`) → fastest.
 *  - manual fast-forward (incl. auto-engage after the human folds) → fast.
 *  - otherwise → watchable.
 */
export function deriveTier(
  fastForward: boolean,
  alwaysFastForward: boolean,
  aiInstant: boolean
): PacingTier {
  if (alwaysFastForward || aiInstant) return 'fastest';
  if (fastForward) return 'fast';
  return 'watchable';
}

/** Scale a watchable-tier duration to the active tier (rounded, never negative). */
export function scale(ms: number, tier: PacingTier): number {
  return Math.max(0, Math.round(ms * TIER_MULTIPLIER[tier]));
}

/**
 * Base (watchable) beat durations. Tune the feel here; nothing else hard-codes
 * these. The deal gates match the community-card slide cascade in
 * `useCommunityCardAnimation` (1.0s/card stagger, ~0.825s slide).
 */
/**
 * Watchable-tier per-action beat (salience-based): routine actions flow,
 * consequential ones land. The `fast` and `fastest` tiers ignore this and use
 * the flat scaled `BEAT.action` so a speed-up stays uniform. Keyed off the
 * acting player's `last_action`.
 */
export const ACTION_BEAT_MS: Record<string, number> = {
  fold: 450,
  check: 450,
  call: 1000,
  bet: 1000,
  raise: 1000,
  all_in: 1400,
};
export const DEFAULT_ACTION_BEAT_MS = 1000;

/**
 * When an action carries AI table talk, hold the beat at least this long
 * (watchable tier). The comment + chip-move land together at the start of the
 * beat; this hold then lingers on that player for a beat or two so the human
 * connects the comment to who said it and sees the action they took, before the
 * table moves on. (The chat bubble also persists on its own reading timer in
 * FloatingChat.) Tune the linger here.
 */
export const COMMENTARY_BEAT_MS = 3200;

/** Salience beat for an action (watchable tier), defaulting unknown actions. */
export function actionBeatMs(lastAction: string | null | undefined): number {
  if (!lastAction) return DEFAULT_ACTION_BEAT_MS;
  return ACTION_BEAT_MS[lastAction.toLowerCase()] ?? DEFAULT_ACTION_BEAT_MS;
}

/**
 * Run-out "stage" splash (the "ALL IN" / "SHOWDOWN" beat that masks the mobile
 * layout reconfigure). Watchable-tier total duration; scaled per tier — `fastest`
 * → 0 skips it so a flushed hand stays instant. The splash component reads the
 * scaled value; `BEAT.stageSplashHold` (below) keeps the reveal/run-out beats
 * behind it so the hole cards + board don't appear until the splash clears.
 */
export const STAGE_SPLASH_MS = 2000;

export const BEAT = {
  /** Flat post-action hold for the fast/fastest tiers (watchable uses ACTION_BEAT_MS). */
  action: 1000,
  /** Hold the reveal beat (hole-card mount + hero commit + matchup read) behind the
   *  run-out splash so the hands don't reveal until it clears. Slightly less than
   *  STAGE_SPLASH_MS so the cards land as the splash fades out. */
  stageSplashHold: 1500,
  /** Flop (3 cards): 2×1.0s stagger + 0.825s slide ≈ 2.825s settle. */
  flopGate: 2825,
  /** Turn / river (1 card): ~0.825s slide. */
  cardGate: 825,
  /** All-in matchup hold: see the hands before the board runs. */
  revealHold: 1500,
  /** Gap between consecutive flop cards (matches the cascade). */
  perCardStagger: 1000,
  /** Within a card's slide, delay until its reaction fires (lands as it settles). */
  reactionAfterCard: 600,
  /** INITIAL (matchup-read) reactions, after the reveal cascade settles. */
  initialReactionDelay: 700,
  /** SHOWDOWN lock-up reactions, after the river card settles. */
  showdownReactionDelay: 900,
  /** Hold on the showdown face before releasing reaction ownership. */
  showdownHold: 2500,
  /** Hero-folded showdown with no run-out: brief breather before the verdict so
   *  the final board lands first (the board already dealt during AI betting, so
   *  this is much shorter than the old 4s backend-paced watch). */
  foldWatch: 1500,
} as const;
