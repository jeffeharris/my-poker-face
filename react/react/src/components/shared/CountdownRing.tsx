import { useMemo } from 'react';
import { motion } from 'framer-motion';
import './CountdownRing.css';

// CK3-style radial countdown. Drains clockwise over a timer's remaining
// lifetime so an otherwise-invisible auto-dismiss timeout becomes legible.
//
//  - Running (timerStartedAt set): the arc animates from whatever fraction
//    is left *now* down to empty over the remaining time. Re-keyed on
//    (timerStartedAt, displayDuration) so a timer that (re)starts — or whose
//    duration changed — restarts cleanly from its true remaining fraction.
//  - Paused (timerStartedAt === null): the ring sits full, signalling
//    "waiting / clock not running" rather than draining against a dead clock.
//
// The caller positions the ring via `className`; the rotate + pointer-events
// live on the base `.countdown-ring` class so every consumer gets a
// top-anchored, gesture-transparent arc for free.
interface CountdownRingProps {
  timerStartedAt: number | null;
  displayDuration: number;
  /** Outer box size in px. Default tuned for the chat bubble corner. */
  size?: number;
  /** Stroke width in px. Scales with `size` at the call site. */
  stroke?: number;
  /** Extra class for positioning (absolute corner placement, z-index). */
  className?: string;
}

export function CountdownRing({
  timerStartedAt,
  displayDuration,
  size = 13,
  stroke = 1.75,
  className,
}: CountdownRingProps) {
  const paused = timerStartedAt === null;
  const radius = (size - stroke) / 2;
  const center = size / 2;

  // Snapshot how much of the ring is left at the moment the timer
  // (re)starts. Only recomputed when the timer identity changes, so it
  // doesn't churn on every parent re-render.
  const initialFraction = useMemo(() => {
    if (timerStartedAt === null) return 1;
    const remaining = displayDuration - (Date.now() - timerStartedAt);
    return Math.max(0, Math.min(1, remaining / displayDuration));
  }, [timerStartedAt, displayDuration]);

  const remainingSec = initialFraction * (displayDuration / 1000);

  return (
    <svg
      className={`countdown-ring${className ? ` ${className}` : ''}`}
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      aria-hidden="true"
    >
      <circle
        className="countdown-ring-track"
        cx={center}
        cy={center}
        r={radius}
        strokeWidth={stroke}
      />
      <motion.circle
        key={`${timerStartedAt}-${displayDuration}`}
        className="countdown-ring-progress"
        cx={center}
        cy={center}
        r={radius}
        strokeWidth={stroke}
        initial={{ pathLength: initialFraction }}
        animate={{ pathLength: paused ? 1 : 0 }}
        transition={paused ? { duration: 0 } : { duration: remainingSec, ease: 'linear' }}
      />
    </svg>
  );
}
