/**
 * PlaybackControls - Transport controls for hand replay
 *
 * Previous / Play|Pause / Next buttons, speed selector, phase jump, action counter.
 */

import { memo, useCallback } from 'react';
import { SkipBack, Play, Pause, SkipForward, ChevronsLeft, ChevronsRight } from 'lucide-react';
import type { HandAction } from './types';

interface PlaybackControlsProps {
  currentIndex: number;
  totalActions: number;
  isPlaying: boolean;
  playbackSpeed: number;
  actions: HandAction[];
  onPrevious: () => void;
  onNext: () => void;
  onTogglePlay: () => void;
  onSetSpeed: (speed: number) => void;
  onSeek: (index: number) => void;
}

const SPEEDS = [1, 2, 5];

export const PlaybackControls = memo(function PlaybackControls({
  currentIndex,
  totalActions,
  isPlaying,
  playbackSpeed,
  actions,
  onPrevious,
  onNext,
  onTogglePlay,
  onSetSpeed,
  onSeek,
}: PlaybackControlsProps) {
  const jumpToPrevPhase = useCallback(() => {
    if (currentIndex <= 0) return;
    const currentPhase = actions[currentIndex]?.phase;
    // Walk backward to find start of previous phase
    for (let i = currentIndex - 1; i >= 0; i--) {
      if (actions[i].phase !== currentPhase) {
        onSeek(i);
        return;
      }
    }
    onSeek(0);
  }, [currentIndex, actions, onSeek]);

  const jumpToNextPhase = useCallback(() => {
    if (currentIndex >= totalActions - 1) return;
    const currentPhase = actions[currentIndex]?.phase;
    for (let i = currentIndex + 1; i < totalActions; i++) {
      if (actions[i].phase !== currentPhase) {
        onSeek(i);
        return;
      }
    }
    onSeek(totalActions - 1);
  }, [currentIndex, totalActions, actions, onSeek]);

  return (
    <div className="playback-controls">
      {/* Phase jump backward */}
      <button
        className="playback-controls__btn playback-controls__btn--phase"
        onClick={jumpToPrevPhase}
        disabled={currentIndex <= 0}
        title="Previous phase"
      >
        <ChevronsLeft size={16} />
      </button>

      {/* Previous action */}
      <button
        className="playback-controls__btn"
        onClick={onPrevious}
        disabled={currentIndex < 0}
        title="Previous action"
      >
        <SkipBack size={16} />
      </button>

      {/* Play/Pause */}
      <button
        className="playback-controls__btn playback-controls__btn--play"
        onClick={onTogglePlay}
        disabled={totalActions === 0}
        title={isPlaying ? 'Pause' : 'Play'}
      >
        {isPlaying ? <Pause size={20} /> : <Play size={20} />}
      </button>

      {/* Next action */}
      <button
        className="playback-controls__btn"
        onClick={onNext}
        disabled={currentIndex >= totalActions - 1}
        title="Next action"
      >
        <SkipForward size={16} />
      </button>

      {/* Phase jump forward */}
      <button
        className="playback-controls__btn playback-controls__btn--phase"
        onClick={jumpToNextPhase}
        disabled={currentIndex >= totalActions - 1}
        title="Next phase"
      >
        <ChevronsRight size={16} />
      </button>

      {/* Speed selector */}
      <div className="playback-controls__speed">
        {SPEEDS.map((s) => (
          <button
            key={s}
            className={`playback-controls__speed-pill ${playbackSpeed === s ? 'playback-controls__speed-pill--active' : ''}`}
            onClick={() => onSetSpeed(s)}
          >
            {s}x
          </button>
        ))}
      </div>

      {/* Action counter */}
      <span className="playback-controls__counter">
        {currentIndex >= 0 ? currentIndex + 1 : 0} / {totalActions}
      </span>
    </div>
  );
});
