/**
 * SalFloater — the mentor's special treatment.
 *
 * Sal "The Clock" Monroe narrates the Scene-0 lesson in table chat; rather than
 * let his lines scroll by in the feed, he pops up as a floating transparent
 * portrait + speech bubble (his cutout is `/sal.png`). Mounted alongside
 * `FloatingChat`, which is gated to skip Sal so his lines come ONLY through here.
 *
 * It plays a QUEUE, not a single slot. Sal often fires several lines back-to-back
 * (the graduation reveal is three; setup + verdict can stack), and the parent's
 * single "most recent message" slot would clobber all but the last. The parent
 * hands us every Sal line in order; we show each for SHOW_MS (tap to skip ahead)
 * and call `onShown` to drop it from the queue so the next appears.
 *
 * In M1, Sal exists only at the pinned Scene-0 table, so a plain sender match is
 * enough to know "Sal is speaking."
 */

import { useEffect, useMemo } from 'react';
import type { ChatMessage } from '../../types';
import { DramaticReserve } from '../shared/DramaticText';
import { calculateDuration, splitSentences } from '../../utils/chatBeats';
import './SalFloater.css';

// Sal's bubble is STATIC text (no char-by-char typing), and his lines are
// multi-sentence coaching the player actually wants to read and absorb — not
// skim. So his dwell is deliberately more generous than the typing bubbles:
// a comfortable read-and-process budget, not bare reading speed. Tap to advance.
const SAL_BASE_MS = 3500; // settle-in beat before the clock starts mattering
const SAL_PER_CHAR_MS = 80; // ~comfortable read + a moment to absorb the advice
const SAL_MIN_MS = 6000; // even a one-liner lingers a beat
const SAL_MAX_MS = 26000; // cap so a long line can't wedge the queue forever

/** How long Sal's bubble lingers — scaled to line length, generous on purpose. */
function readMs(text: string): number {
  const chars = (text || '').trim().length;
  const ms = chars * SAL_PER_CHAR_MS + SAL_BASE_MS;
  return Math.min(SAL_MAX_MS, Math.max(SAL_MIN_MS, ms));
}

interface SalFloaterProps {
  /** Sal's pending lines, oldest first. The head is shown; `onShown` shifts it. */
  queue: ChatMessage[];
  onShown: (id: string) => void;
}

export function SalFloater({ queue, onShown }: SalFloaterProps) {
  const current = queue.length > 0 ? queue[0] : null;
  const currentId = current?.id ?? null;
  // Beat-ify his line: each sentence on its own line so the print-style renderer
  // types it out with a pause after every sentence (Sal speaks in measured beats).
  const beatText = useMemo(
    () => (current ? splitSentences(current.message) : ''),
    [current]
  );
  // Tied to currentId: only changes when the head line changes, so the timer
  // isn't reset by unrelated queue churn while a line is showing. The bubble must
  // outlast the full type-out + pauses, so floor the generous read budget at the
  // animation's own duration.
  const showMs = current ? Math.max(readMs(current.message), calculateDuration(beatText)) : 0;

  useEffect(() => {
    if (!currentId) return undefined;
    const t = setTimeout(() => onShown(currentId), showMs);
    return () => clearTimeout(t);
  }, [currentId, showMs, onShown]);

  if (!current) return null;

  return (
    <div
      className="sal-floater"
      onClick={() => onShown(current.id)}
      role="button"
      aria-label="Sal says"
    >
      <img className="sal-floater__img" src="/sal.png" alt="Sal Monroe" />
      <div className="sal-floater__bubble">
        <span className="sal-floater__name">Sal</span>
        <DramaticReserve text={beatText} />
      </div>
    </div>
  );
}
