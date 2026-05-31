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

import { useEffect } from 'react';
import type { ChatMessage } from '../../types';
import { parseMessageInline } from '../../utils/messages';
import {
  TYPING_SPEED_MS,
  READING_BUFFER_MS,
  MESSAGE_BASE_DURATION_MS,
  MESSAGE_MIN_DURATION_MS,
  MESSAGE_MAX_DURATION_MS,
} from '../../config/timing';
import './SalFloater.css';

/**
 * How long Sal's bubble lingers — scaled to the line length so his longer
 * coaching lines get enough reading time (a fixed timer made the multi-sentence
 * setups auto-dismiss at roughly half the time needed). Same per-char budget as
 * FloatingChat so the pacing matches the rest of the table; clamped to its
 * min/max. Tap the floater to advance early.
 */
function readMs(text: string): number {
  const chars = (text || '').trim().length;
  const ms = chars * (TYPING_SPEED_MS + READING_BUFFER_MS) + MESSAGE_BASE_DURATION_MS;
  return Math.min(MESSAGE_MAX_DURATION_MS, Math.max(MESSAGE_MIN_DURATION_MS, ms));
}

interface SalFloaterProps {
  /** Sal's pending lines, oldest first. The head is shown; `onShown` shifts it. */
  queue: ChatMessage[];
  onShown: (id: string) => void;
}

export function SalFloater({ queue, onShown }: SalFloaterProps) {
  const current = queue.length > 0 ? queue[0] : null;
  const currentId = current?.id ?? null;
  // Tied to currentId: only changes when the head line changes, so the timer
  // isn't reset by unrelated queue churn while a line is showing.
  const showMs = current ? readMs(current.message) : 0;

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
        {parseMessageInline(current.message)}
      </div>
    </div>
  );
}
