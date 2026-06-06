import { useState, useRef, useLayoutEffect, useMemo } from 'react';
import { useGameStore } from '../stores/gameStore';

interface CardAnimationState {
  shouldAnimate: boolean;
  delay: number; // seconds before animation starts
  duration: number; // seconds for animation (proportional to distance)
}

/**
 * Per-card community-card slide animation, driven by the hand sequencer's
 * authoritative deal trigger (`store.cardDeal`) rather than inferring a deal from
 * card-count deltas. The sequencer fires one monotonic `token` per real deal
 * beat, so the board animates exactly once per street — a re-render, a duplicate
 * state push, or a cold-load that re-asserts the board can't re-deal it. (The
 * engine's own `communityCount` baseline already drops duplicate deals upstream;
 * the token is the render-side half of the same guarantee.)
 *
 * Flop (3 cards): 0s/1s/2s cascade delays, 0.825s duration each.
 * Turn/River (1 card): 0s delay, 0.825s duration.
 */
export function useCommunityCardAnimation(totalCards: number): CardAnimationState[] {
  const cardDeal = useGameStore((s) => s.cardDeal);

  const [animatingIndices, setAnimatingIndices] = useState<Set<number>>(new Set());
  const lastTokenRef = useRef(0);
  const prevCardCountRef = useRef(totalCards);
  const clearTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Animate the cards a new deal token brings in. Keyed on the token (not the
  // count), so the same deal never animates twice. layout effect (not effect) so
  // the decision is committed before paint — otherwise the card paints statically
  // in its final spot for one frame, then flies in (a visible flash).
  useLayoutEffect(() => {
    if (!cardDeal || cardDeal.token === lastTokenRef.current) return;
    lastTokenRef.current = cardDeal.token;

    const { count, total } = cardDeal;
    const newIndices = new Set<number>();
    for (let i = total - count; i < total; i++) newIndices.add(i);
    setAnimatingIndices(newIndices);

    // Clear once the cascade finishes: last card starts at (count-1)×1s and
    // slides for 0.825s, plus a small tail.
    const clearAfter = ((count - 1) * 1.0 + 0.825 + 0.2) * 1000;
    if (clearTimerRef.current) clearTimeout(clearTimerRef.current);
    clearTimerRef.current = setTimeout(() => setAnimatingIndices(new Set()), clearAfter);
  }, [cardDeal]);

  // New hand (board shrank) — drop any in-flight animation + pending clear.
  useLayoutEffect(() => {
    if (totalCards < prevCardCountRef.current) {
      setAnimatingIndices(new Set());
      if (clearTimerRef.current) {
        clearTimeout(clearTimerRef.current);
        clearTimerRef.current = undefined;
      }
    }
    prevCardCountRef.current = totalCards;
  }, [totalCards]);

  // Cleanup on unmount.
  useLayoutEffect(() => {
    return () => {
      if (clearTimerRef.current) clearTimeout(clearTimerRef.current);
    };
  }, []);

  // Build animation state for all 5 community-card positions.
  return useMemo(
    () =>
      Array.from({ length: 5 }, (_, index) => {
        if (!animatingIndices.has(index)) {
          return { shouldAnimate: false, delay: 0, duration: 0.55 };
        }
        // Position within the newly dealt batch (0, 1, 2 for the flop).
        const batchStart = totalCards - animatingIndices.size;
        const positionInBatch = index - batchStart;
        return { shouldAnimate: true, delay: positionInBatch * 1.0, duration: 0.825 };
      }),
    [animatingIndices, totalCards]
  );
}
