import { animate, useReducedMotion } from 'framer-motion';
import { useEffect, useRef } from 'react';

interface CountUpProps {
  /** Target value. The component tweens from whatever it last showed to this. */
  value: number;
  /** If set, the first paint starts at this value and tweens up to `value` on
   *  mount — e.g. `from={0}` so a bet chip counts up the moment it appears.
   *  Omit (the default) to snap to `value` on mount and only animate changes. */
  from?: number;
  /** Tween duration in seconds. */
  durationSec?: number;
  /** Group thousands with separators (e.g. 12,500). Use for cash/bankroll
   *  readouts that already render with `toLocaleString()`; leave off for the
   *  table's plain pot/stack/bet numbers. */
  useGrouping?: boolean;
  className?: string;
}

function format(n: number, group: boolean): string {
  const rounded = Math.round(n);
  return group ? rounded.toLocaleString() : String(rounded);
}

/**
 * Animated number that counts from its previous value to `value`.
 *
 * It tweens the text content directly through a ref (not React state), so a
 * 60fps count-up never re-renders this component's parent — important on the
 * live poker table where the pot/stack sit among hot, frequently-updating UI.
 * Interrupts are smooth: a new target tweens from the value currently on screen.
 * Honors `prefers-reduced-motion` by snapping straight to the value.
 *
 * Renders just the number — callers own any prefix/suffix (e.g. `$`).
 */
export function CountUp({
  value,
  from,
  durationSec = 0.42,
  useGrouping = false,
  className,
}: CountUpProps) {
  const reduce = useReducedMotion();
  const ref = useRef<HTMLSpanElement>(null);
  // The value currently painted — the tween starts here so mid-flight target
  // changes don't snap. Seeds from `from` when given (count up on first mount).
  const shown = useRef(from ?? value);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const start = shown.current;
    if (reduce || start === value) {
      shown.current = value;
      el.textContent = format(value, useGrouping);
      return;
    }
    const controls = animate(start, value, {
      duration: durationSec,
      ease: [0.16, 1, 0.3, 1], // ease-out-expo, matches --ease-out-expo token
      onUpdate: (v) => {
        shown.current = v;
        el.textContent = format(v, useGrouping);
      },
    });
    return () => controls.stop();
  }, [value, reduce, durationSec, useGrouping]);

  return (
    <span ref={ref} className={className}>
      {format(from ?? value, useGrouping)}
    </span>
  );
}
