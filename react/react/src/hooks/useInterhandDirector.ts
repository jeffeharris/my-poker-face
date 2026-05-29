import { useCallback, useEffect, useRef, useState } from 'react';
import { INTERHAND_TIMING } from '../constants/interhandTiming';

interface UseInterhandDirectorParams {
  /** True while the winner overlay is showing (the "result" beat). */
  hasWinner: boolean;
  /** The store's current hand number; increments when the next hand deals. */
  handNumber: number;
  shuffleMinMs?: number;
  shuffleMaxMs?: number;
}

interface InterhandDirector {
  /** True while the shuffle beat owns the screen (between result and next hand). */
  isShuffling: boolean;
  /** Call when the result beat finishes (auto-dismiss or Continue) to hand off
   *  to the shuffle beat. Pair it with clearing the winner so the two overlays
   *  are never up at once. */
  beginShuffle: () => void;
}

/**
 * useInterhandDirector — owns the mobile between-hand timeline so the client,
 * not the backend's variable phase pacing, decides the beat.
 *
 * The sequence is `result → shuffle → next hand`, with the result beat owned by
 * the winner overlay (which calls `beginShuffle` when its hold elapses or the
 * player taps Continue). This hook owns only the shuffle beat:
 *
 *   - it never overlaps the result beat (shuffle starts only once the winner is
 *     dismissed via `beginShuffle`),
 *   - it holds for `shuffleMinMs` so a fast backend can't flash it, and
 *   - it exits once the next hand has actually arrived (handNumber moved past
 *     the hand that just ended) — or after `shuffleMaxMs` as a safety net so a
 *     game that never deals again (tournament end, disconnect) can't hang.
 */
export function useInterhandDirector({
  hasWinner,
  handNumber,
  shuffleMinMs = INTERHAND_TIMING.shuffleMinMs,
  shuffleMaxMs = INTERHAND_TIMING.shuffleMaxMs,
}: UseInterhandDirectorParams): InterhandDirector {
  const [isShuffling, setIsShuffling] = useState(false);
  const [minElapsed, setMinElapsed] = useState(false);

  // The hand that just ended — captured on the rising edge of `hasWinner`,
  // while handNumber still reads the completed hand (it increments later, when
  // the next hand initializes). Shuffle exits once handNumber moves past this.
  const endedHandRef = useRef(handNumber);
  const prevHasWinnerRef = useRef(false);

  useEffect(() => {
    if (hasWinner && !prevHasWinnerRef.current) {
      endedHandRef.current = handNumber;
    }
    prevHasWinnerRef.current = hasWinner;
  }, [hasWinner, handNumber]);

  const beginShuffle = useCallback(() => setIsShuffling(true), []);

  // Minimum-beat and safety-cap timers, scoped to each shuffle.
  useEffect(() => {
    if (!isShuffling) return;
    setMinElapsed(false);
    const minTimer = setTimeout(() => setMinElapsed(true), shuffleMinMs);
    const maxTimer = setTimeout(() => setIsShuffling(false), shuffleMaxMs);
    return () => {
      clearTimeout(minTimer);
      clearTimeout(maxTimer);
    };
  }, [isShuffling, shuffleMinMs, shuffleMaxMs]);

  // Exit once the next hand is dealt and the minimum beat has been shown.
  useEffect(() => {
    if (isShuffling && minElapsed && handNumber !== endedHandRef.current) {
      setIsShuffling(false);
    }
  }, [isShuffling, minElapsed, handNumber]);

  return { isShuffling, beginShuffle };
}
