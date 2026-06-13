import { useEffect } from 'react';
import { type SwipeDir } from './swipe/SwipeDeck';
import { pct, type Grade } from './preflop/preflopUtils';

// Full-screen result wash. The point: the verdict is impossible to miss without
// looking down, and each solver frequency sits at the EDGE you swiped — fold on
// the left, call up top, raise on the right — so you read the number where your
// thumb just was. Tints the whole screen the verdict colour, then auto-advances.

// How long the wash holds before the next card deals. Wrong answers linger so you
// actually read the correction; tapping the wash skips the wait.
const HOLD_MS: Record<Grade['verdict'], number> = { good: 750, thin: 1100, leak: 1900 };

const ARROW: Record<SwipeDir, string> = { left: '←', up: '↑', right: '→' };

export interface OverlayFreq {
  dir: SwipeDir;
  label: string;
  value: number; // 0..1 solver frequency
}

interface DrillResultOverlayProps {
  verdict: Grade['verdict'];
  heading: string;
  freqs: OverlayFreq[];
  /** The direction the player swiped — its frequency is highlighted. */
  chosen: SwipeDir | null;
  onDone: () => void;
}

export function DrillResultOverlay({
  verdict,
  heading,
  freqs,
  chosen,
  onDone,
}: DrillResultOverlayProps) {
  useEffect(() => {
    const t = setTimeout(onDone, HOLD_MS[verdict]);
    return () => clearTimeout(t);
  }, [verdict, onDone]);

  return (
    <button
      type="button"
      className={`drill-result drill-result--${verdict}`}
      onClick={onDone}
      aria-label="Continue to next hand"
    >
      {freqs.map((f) => (
        <span
          key={f.dir}
          className={
            `drill-result__freq drill-result__freq--${f.dir}` +
            (chosen === f.dir ? ' drill-result__freq--chosen' : '')
          }
        >
          <span className="drill-result__arrow">{ARROW[f.dir]}</span>
          <span className="drill-result__freq-label">{f.label}</span>
          <span className="drill-result__freq-pct">{pct(f.value)}%</span>
        </span>
      ))}
      <span className="drill-result__heading">{heading}</span>
    </button>
  );
}
