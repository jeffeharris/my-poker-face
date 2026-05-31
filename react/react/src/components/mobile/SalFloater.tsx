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
import { renderInlineActions } from '../../utils/chatText';
import './SalFloater.css';

const SHOW_MS = 6500;

interface SalFloaterProps {
  /** Sal's pending lines, oldest first. The head is shown; `onShown` shifts it. */
  queue: ChatMessage[];
  onShown: (id: string) => void;
}

export function SalFloater({ queue, onShown }: SalFloaterProps) {
  const current = queue.length > 0 ? queue[0] : null;
  const currentId = current?.id ?? null;

  useEffect(() => {
    if (!currentId) return undefined;
    const t = setTimeout(() => onShown(currentId), SHOW_MS);
    return () => clearTimeout(t);
  }, [currentId, onShown]);

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
        {renderInlineActions(current.message)}
      </div>
    </div>
  );
}
