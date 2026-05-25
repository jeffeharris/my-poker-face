/**
 * TimelineScrubber - Horizontal progress bar with phase markers
 *
 * Shows action progress through the hand with phase dividers.
 * Click-to-seek maps click position to action index.
 */

import { memo, useCallback, useRef } from 'react';
import type { HandAction } from './types';

interface TimelineScrubberProps {
  actions: HandAction[];
  currentIndex: number;
  onSeek: (index: number) => void;
}

export const TimelineScrubber = memo(function TimelineScrubber({
  actions,
  currentIndex,
  onSeek,
}: TimelineScrubberProps) {
  const trackRef = useRef<HTMLDivElement>(null);

  // Find phase boundary indices
  const phaseMarkers: { phase: string; index: number }[] = [];
  let lastPhase = '';
  for (let i = 0; i < actions.length; i++) {
    if (actions[i].phase !== lastPhase) {
      phaseMarkers.push({ phase: actions[i].phase, index: i });
      lastPhase = actions[i].phase;
    }
  }

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const track = trackRef.current;
      if (!track || actions.length === 0) return;

      const rect = track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const index = Math.round(ratio * (actions.length - 1));
      onSeek(index);
    },
    [actions.length, onSeek]
  );

  const totalActions = actions.length;
  const progressPercent = totalActions > 0 ? ((currentIndex + 1) / totalActions) * 100 : 0;

  return (
    <div className="timeline-scrubber">
      {/* Phase labels */}
      <div className="timeline-scrubber__phases">
        {phaseMarkers.map(({ phase, index }) => {
          const leftPercent = totalActions > 1 ? (index / (totalActions - 1)) * 100 : 0;
          return (
            <span
              key={phase}
              className="timeline-scrubber__phase-label"
              style={{ left: `${leftPercent}%` }}
            >
              {phase.replace('_', ' ')}
            </span>
          );
        })}
      </div>

      {/* Track */}
      <div ref={trackRef} className="timeline-scrubber__track" onClick={handleClick}>
        {/* Phase dividers */}
        {phaseMarkers.slice(1).map(({ phase, index }) => {
          const leftPercent = totalActions > 1 ? (index / (totalActions - 1)) * 100 : 0;
          return (
            <div
              key={phase}
              className="timeline-scrubber__divider"
              style={{ left: `${leftPercent}%` }}
            />
          );
        })}

        {/* Progress fill */}
        <div className="timeline-scrubber__fill" style={{ width: `${progressPercent}%` }} />

        {/* Thumb */}
        {currentIndex >= 0 && (
          <div className="timeline-scrubber__thumb" style={{ left: `${progressPercent}%` }} />
        )}
      </div>
    </div>
  );
});
