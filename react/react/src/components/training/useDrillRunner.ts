import { useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import { type SwipeDeckHandle, type SwipeDir } from './swipe/SwipeDeck';
import { drawNext, type Spot, type Grade } from './preflop/preflopUtils';

// The grade/flow engine shared by every preflop swipe drill. A drill supplies
// its loaded `pool` and a small config (how a swipe maps to a poker action, any
// extra grade-request fields); the hook owns the deck ref, the grade request,
// the running tally, and keyboard/action-bar parity. Keeping it in one place is
// what guarantees all the drills behave identically.

export interface DrillRunnerConfig {
  /** Map a swipe direction to the poker action sent for grading. */
  dirToAction: (dir: SwipeDir) => string;
  /** Map an action-bar button to the swipe direction that performs it. */
  actionToDir: (action: string) => SwipeDir;
  /** Extra fields merged into the grade request body (e.g. archetype). */
  answerExtra?: (spot: Spot) => Record<string, unknown>;
  /** Whether the upward (call) direction is live — enables ArrowUp parity. */
  allowUp?: boolean;
}

export function useDrillRunner(pool: Spot[], cfg: DrillRunnerConfig) {
  const { dirToAction, actionToDir, answerExtra, allowUp } = cfg;
  const deckRef = useRef<SwipeDeckHandle>(null);

  const [grade, setGrade] = useState<Grade | null>(null);
  const [chosenDir, setChosenDir] = useState<SwipeDir | null>(null);
  const [grading, setGrading] = useState(false);
  const [solid, setSolid] = useState(0);
  const [answered, setAnswered] = useState(0);

  // Reset the run whenever the pool is (re)loaded.
  useEffect(() => {
    setGrade(null);
    setChosenDir(null);
    setSolid(0);
    setAnswered(0);
  }, [pool]);

  const draw = useCallback((avoid: Spot | null) => drawNext(pool, avoid), [pool]);

  const onSwipe = useCallback(
    async (spot: Spot, dir: SwipeDir) => {
      const action = dirToAction(dir);
      setChosenDir(dir);
      setGrading(true);
      try {
        const resp = await fetch(`${config.API_URL}/api/coach/drill/answer`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            scenario: spot.scenario,
            position: spot.position,
            hand: spot.hand,
            action,
            ...(answerExtra ? answerExtra(spot) : {}),
          }),
        });
        if (!resp.ok) throw new Error(`grade ${resp.status}`);
        const g: Grade = await resp.json();
        setGrade(g);
        setAnswered((n) => n + 1);
        if (g.verdict === 'good') setSolid((n) => n + 1);
      } catch (err) {
        // Grading failed (network / limiter). The card is already flung off-screen,
        // so recover instead of soft-locking: drop it, rise the next, and tell the
        // user this spot didn't count rather than failing silently.
        logger.error('Failed to grade answer:', err);
        toast.error("Couldn't grade that hand — skipping it.");
        deckRef.current?.advance();
      } finally {
        setGrading(false);
      }
    },
    [dirToAction, answerExtra]
  );

  const next = useCallback(() => {
    setGrade(null);
    deckRef.current?.advance();
  }, []);

  // The action bar feeds the deck: a tap flings the card the matching way, which
  // grades + advances through the same path as a swipe.
  const onBarAction = useCallback(
    (action: string) => {
      if (grade || grading) return;
      deckRef.current?.swipe(actionToDir(action));
    },
    [grade, grading, actionToDir]
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (grade || grading) return;
      if (e.key === 'ArrowRight') deckRef.current?.swipe('right');
      else if (e.key === 'ArrowLeft') deckRef.current?.swipe('left');
      else if (allowUp && e.key === 'ArrowUp') deckRef.current?.swipe('up');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [grade, grading, allowUp]);

  return {
    deckRef,
    draw,
    grade,
    chosenDir,
    grading,
    interactive: !grade && !grading,
    onSwipe,
    next,
    onBarAction,
    solid,
    answered,
  };
}
