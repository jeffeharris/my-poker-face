import { useState, useRef, useEffect } from 'react';

interface CardAnimationState {
  shouldAnimate: boolean;
  delay: number;    // seconds before animation starts
  duration: number; // seconds for animation (proportional to distance)
}

/**
 * Hook that tracks when new community cards are dealt and returns
 * per-card animation state with cascade delays and proportional durations.
 *
 * Flop (3 cards): 0s/1s/2s delays, 0.50s/0.60s/0.70s durations
 * Turn/River (1 card): 0s delay, 0.55s duration
 */
export function useCommunityCardAnimation(
  newlyDealtCount: number | undefined,
  totalCards: number,
): CardAnimationState[] {
  const [animatingIndices, setAnimatingIndices] = useState<Set<number>>(new Set());
  const prevCardCountRef = useRef(0);
  const isInitialMount = useRef(true);
  const clearTimerRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    const count = newlyDealtCount ?? 0;

    // On initial mount with cards already present (e.g. page refresh), skip animation
    if (isInitialMount.current) {
      isInitialMount.current = false;
      prevCardCountRef.current = totalCards;
      return;
    }

    // New cards were dealt
    if (count > 0 && totalCards > prevCardCountRef.current) {
      const newIndices = new Set<number>();
      for (let i = totalCards - count; i < totalCards; i++) {
        newIndices.add(i);
      }
      setAnimatingIndices(newIndices);

      // Clear animation state after all animations complete
      // For flop: last card starts at 2s + 0.825s duration = 2.825s
      const maxDelay = (count - 1) * 1.0;
      const clearAfter = (maxDelay + 0.825 + 0.2) * 1000;

      if (clearTimerRef.current) {
        clearTimeout(clearTimerRef.current);
      }
      clearTimerRef.current = setTimeout(() => {
        setAnimatingIndices(new Set());
      }, clearAfter);
    }

    // Cards decreased (new hand started) - clear animations and pending timers
    if (totalCards < prevCardCountRef.current) {
      setAnimatingIndices(new Set());
      if (clearTimerRef.current) {
        clearTimeout(clearTimerRef.current);
        clearTimerRef.current = undefined;
      }
    }

    prevCardCountRef.current = totalCards;

    return () => {
      if (clearTimerRef.current) {
        clearTimeout(clearTimerRef.current);
        clearTimerRef.current = undefined;
      }
    };
  }, [newlyDealtCount, totalCards]);

  // Build animation state array for each card position
  return Array.from({ length: totalCards }, (_, index) => {
    if (!animatingIndices.has(index)) {
      return { shouldAnimate: false, delay: 0, duration: 0.55 };
    }

    // Position within the newly dealt batch (0, 1, 2 for flop)
    const batchStart = totalCards - animatingIndices.size;
    const positionInBatch = index - batchStart;

    // Cascade delay: 1 second between each card
    const delay = positionInBatch * 1.0;

    // All cards travel the same distance (-100vw relative to their own position),
    // so a fixed duration gives consistent velocity.
    const duration = 0.825;

    return { shouldAnimate: true, delay, duration };
  });
}
