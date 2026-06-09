/**
 * RunoutStageSplash — a brief full-screen beat ("ALL IN" / "SHOWDOWN") that
 * drops over the mobile table the instant it enters run-out "stage" mode. It
 * masks the layout reconfigure happening behind it (so the simultaneous slides
 * never read as jank) and adds a dramatic beat. Fires once on the rising edge of
 * `stageActive` and fades itself out (~1s). Purely presentational.
 */
import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import './RunoutStageSplash.css';

export function RunoutStageSplash({
  stageActive,
  label,
  durationMs,
}: {
  stageActive: boolean;
  label: string;
  /** Tier-scaled splash duration (ms). <= 0 skips the splash entirely (the
   *  fast-forward tiers want no presentational beat). */
  durationMs: number;
}) {
  const [shownLabel, setShownLabel] = useState<string | null>(null);
  const wasActive = useRef(false);

  useEffect(() => {
    if (stageActive && !wasActive.current) {
      wasActive.current = true;
      if (durationMs <= 0) return undefined; // fast tiers: no splash beat
      setShownLabel(label);
      const t = window.setTimeout(() => setShownLabel(null), durationMs);
      return () => window.clearTimeout(t);
    }
    // Reset the edge latch once the stage clears so the next run-out re-fires.
    if (!stageActive) wasActive.current = false;
    return undefined;
  }, [stageActive, label, durationMs]);

  if (!shownLabel) return null;

  // Drive the in→hold→out animation off the tier-scaled duration (keyframes are
  // percentage-based, so they stretch/shrink with it).
  const animStyle = { animationDuration: `${durationMs}ms` } as CSSProperties;

  return (
    <div
      className="runout-stage-splash"
      data-testid="runout-stage-splash"
      aria-hidden="true"
      style={animStyle}
    >
      <span className="runout-stage-splash__label" style={animStyle}>
        {shownLabel}
      </span>
    </div>
  );
}
