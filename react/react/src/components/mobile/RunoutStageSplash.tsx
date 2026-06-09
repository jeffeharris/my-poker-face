/**
 * RunoutStageSplash — a brief full-screen beat ("ALL IN" / "SHOWDOWN") that
 * drops over the mobile table the instant it enters run-out "stage" mode. It
 * masks the layout reconfigure happening behind it (so the simultaneous slides
 * never read as jank) and adds a dramatic beat. Fires once on the rising edge of
 * `stageActive` and fades itself out (~1s). Purely presentational.
 */
import { useEffect, useRef, useState } from 'react';
import './RunoutStageSplash.css';

const SPLASH_MS = 1000;

export function RunoutStageSplash({
  stageActive,
  label,
}: {
  stageActive: boolean;
  label: string;
}) {
  const [shownLabel, setShownLabel] = useState<string | null>(null);
  const wasActive = useRef(false);

  useEffect(() => {
    if (stageActive && !wasActive.current) {
      wasActive.current = true;
      setShownLabel(label);
      const t = window.setTimeout(() => setShownLabel(null), SPLASH_MS);
      return () => window.clearTimeout(t);
    }
    // Reset the edge latch once the stage clears so the next run-out re-fires.
    if (!stageActive) wasActive.current = false;
    return undefined;
  }, [stageActive, label]);

  if (!shownLabel) return null;

  return (
    <div className="runout-stage-splash" data-testid="runout-stage-splash" aria-hidden="true">
      <span className="runout-stage-splash__label">{shownLabel}</span>
    </div>
  );
}
