import { useEffect, useRef, useState } from 'react';
import { RUNOUT_TIMING } from '../constants/runoutTiming';

interface UseWinnerRevealGateParams {
  /** True once the backend's winner_announcement has set winnerInfo. */
  hasWinner: boolean;
  /** True when this hand went to a showdown (vs. a fold-out walk). */
  isShowdown: boolean;
  /** Store hand number — a change means the next hand dealt; re-arm the gate. */
  handNumber: number;
  /** True during an all-in run-out (the runout director owns the beat). */
  runItOut: boolean | undefined;
  /** True when the human folded — a spectator to the AIs' showdown. */
  heroFolded: boolean;
  /** Store flag: the runout director is currently the authoritative beat. It
   *  flips false at the director's showdown hold, which is our "all-in done". */
  runoutDirectorActive: boolean;
  /** The player has opted to speed through (manual FF / always-FF / instant AI).
   *  Reusing fast-forward as the Skip: when set, never hold — reveal at once. */
  rushing: boolean;
}

/**
 * useWinnerRevealGate — holds the showdown winner overlay until the run-out /
 * fold play-out has visually finished, so the verdict doesn't spoil the board
 * and reactions the player is still watching. Sibling to `useRunoutDirector` /
 * `useInterhandDirector`: the *client*, not the backend's emit timing, decides
 * when the result beat starts.
 *
 * The backend fires `winner_announcement` the moment the hand is recorded —
 * which, for a folded spectator (or a fast backend), lands while the board is
 * still settling and the AI chat is just starting. Mounting the overlay on that
 * raw event is the "jump straight to the winner" spoiler. This gate defers it.
 *
 * Returns `holdWinner`: true while the overlay should stay suppressed even
 * though winnerInfo is set. The table renders the overlay only when it's false.
 *
 * Rules (first match wins):
 *  - Rushing → never hold. The player engaged fast-forward; that IS the Skip,
 *    so reveal immediately (preserves the "speed through after I fold" pref).
 *  - All-in run-out → hold while the runout director owns the beat
 *    (`runoutDirectorActive`); it releases at its showdown hold and the verdict
 *    lands right after.
 *  - Folded spectator to a showdown → hold for a short watch beat
 *    (`foldShowdownWatchMs`) so the final board + reactions land first.
 *  - Otherwise (hero played to showdown, fold-out walk) → no hold; unchanged.
 *
 * A backstop timer (`revealGateSafetyMs`) guarantees the overlay can never be
 * suppressed forever, even if a hold signal hangs.
 */
export function useWinnerRevealGate({
  hasWinner,
  isShowdown,
  handNumber,
  runItOut,
  heroFolded,
  runoutDirectorActive,
  rushing,
}: UseWinnerRevealGateParams): { holdWinner: boolean } {
  const [foldWatchElapsed, setFoldWatchElapsed] = useState(false);
  const [safetyElapsed, setSafetyElapsed] = useState(false);
  // The hand we've already armed the gate for. Re-arm only on the rising edge of
  // a new hand's winner so toggles mid-beat don't restart the timers.
  const armedHandRef = useRef<number | null>(null);

  const watchableAllIn = hasWinner && isShowdown && !!runItOut;
  const watchableFold = hasWinner && isShowdown && heroFolded && !runItOut;

  useEffect(() => {
    if (!hasWinner) {
      armedHandRef.current = null;
      setFoldWatchElapsed(false);
      setSafetyElapsed(false);
      return;
    }
    if (armedHandRef.current === handNumber) return; // already armed this hand
    armedHandRef.current = handNumber;
    setFoldWatchElapsed(false);
    setSafetyElapsed(false);

    const timers: number[] = [];
    timers.push(window.setTimeout(() => setSafetyElapsed(true), RUNOUT_TIMING.revealGateSafetyMs));
    // Only the folded-spectator path is timer-driven; the all-in path keys off
    // the director's own release, and the other paths never hold.
    if (watchableFold) {
      timers.push(
        window.setTimeout(() => setFoldWatchElapsed(true), RUNOUT_TIMING.foldShowdownWatchMs)
      );
    }
    return () => timers.forEach((id) => clearTimeout(id));
    // watchableFold is captured intentionally at arm time (rising edge), not a
    // re-run trigger — the ref guard owns re-arming.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasWinner, handNumber]);

  let holdWinner = false;
  if (hasWinner && isShowdown && !rushing && !safetyElapsed) {
    if (watchableAllIn) holdWinner = runoutDirectorActive;
    else if (watchableFold) holdWinner = !foldWatchElapsed;
  }

  return { holdWinner };
}
