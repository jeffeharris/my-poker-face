/**
 * interhandTiming — the single source of truth for the mobile between-hand beat.
 *
 * The interhand transition is client-owned and deterministic: the result beat
 * (who won) holds for a minimum so the common fold-out case can't flash, then
 * the shuffle beat covers the wait for the backend to deal the next hand.
 * Tune the feel here; nothing else hard-codes these durations.
 *
 * Defaults are the "Snappy" profile: brisk on routine hands, a touch longer on
 * showdowns. All values are milliseconds.
 */
export const INTERHAND_TIMING = {
  /** Fold-out (walk) result: how long the winner card holds before auto-advancing. */
  foldoutResultMs: 1500,
  /** Showdown result: total hold before auto-advancing (includes the reveal). */
  showdownResultMs: 7500,
  /** Showdown: delay before the revealed cards animate in. */
  showdownCardRevealMs: 800,
  /** Shuffle beat: minimum visible time, so a fast backend can't flash it. */
  shuffleMinMs: 1200,
  /** Shuffle beat: safety cap — leave even if the next hand never signals. */
  shuffleMaxMs: 12000,
} as const;
