/**
 * HandReplayViewer - Main replay component with state management
 *
 * Manages currentActionIndex, playback, and computes VisualState.
 * Handles keyboard shortcuts (Left/Right arrows, Space for play/pause).
 */

import { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import { ReplayPokerTable } from './ReplayPokerTable';
import { TimelineScrubber } from './TimelineScrubber';
import { PlaybackControls } from './PlaybackControls';
import { ActionLog } from './ActionLog';
import { EnrichmentPanel } from './EnrichmentPanel';
import type { HandReplayData, VisualState, VisualPlayer } from './types';

interface HandReplayViewerProps {
  data: HandReplayData;
}

/** Compute the visual state of the table at a given action index */
function computeVisualState(data: HandReplayData, actionIndex: number): VisualState {
  // Initialize players from starting state
  const playerMap = new Map<string, VisualPlayer>();
  for (const p of data.players) {
    playerMap.set(p.name, {
      name: p.name,
      seat_index: p.seat_index,
      stack: p.starting_stack,
      bet: 0,
      hole_cards: p.hole_cards,
      position: p.position,
      is_folded: false,
      is_all_in: false,
      is_current: false,
      last_action: null,
    });
  }

  let currentPhase = 'PRE_FLOP';
  let communityCards: string[] = [];
  let pot = 0;
  let currentPlayerName: string | null = null;

  // Replay actions 0..actionIndex
  for (let i = 0; i <= actionIndex && i < data.actions.length; i++) {
    const action = data.actions[i];

    // Phase change: reset bets, reveal community cards
    if (action.phase !== currentPhase) {
      // Reset bets for all players on phase change
      for (const player of playerMap.values()) {
        player.bet = 0;
      }
      currentPhase = action.phase;

      // Reveal community cards — accumulate across phases
      const phaseOrder = ['PRE_FLOP', 'FLOP', 'TURN', 'RIVER'];
      const accumulated: string[] = [];
      for (const p of phaseOrder) {
        const cards = data.community_cards_by_phase[p];
        if (cards) accumulated.push(...cards);
        if (p === currentPhase) break;
      }
      communityCards =
        accumulated.length > 0
          ? accumulated
          : action.community_cards_visible?.length
            ? [...action.community_cards_visible]
            : communityCards;
    }

    const player = playerMap.get(action.player_name);
    if (player) {
      // Deduct amount from stack, add to bet
      player.stack -= action.amount;
      player.bet += action.amount;

      // Track fold / all-in
      if (action.action === 'fold') {
        player.is_folded = true;
      }
      if (action.action === 'all_in') {
        player.is_all_in = true;
      }

      // Set last action only on current action (clear others)
      player.last_action = i === actionIndex ? action.action : player.last_action;
    }

    // Update pot from action
    pot = action.pot_after;

    // Current player highlight
    if (i === actionIndex) {
      currentPlayerName = action.player_name;
    }
  }

  // Set current player flag
  for (const player of playerMap.values()) {
    player.is_current = player.name === currentPlayerName;
  }

  // If no actions yet (actionIndex < 0), use pre-flop community cards
  if (actionIndex < 0) {
    communityCards = [];
    currentPhase = 'PRE_FLOP';
  }

  return {
    players: Array.from(playerMap.values()),
    community_cards: communityCards,
    pot,
    phase: currentPhase,
    current_player_name: currentPlayerName,
  };
}

export function HandReplayViewer({ data }: HandReplayViewerProps) {
  const [currentIndex, setCurrentIndex] = useState(-1);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const totalActions = data.actions.length;

  // Compute visual state
  const visualState = useMemo(() => computeVisualState(data, currentIndex), [data, currentIndex]);

  // Get current enrichment
  const currentEnrichment = useMemo(() => {
    if (currentIndex < 0 || !data.enrichment) return null;
    const action = data.actions[currentIndex];
    return data.enrichment[action.index] ?? null;
  }, [data, currentIndex]);

  // Playback controls
  const goNext = useCallback(() => {
    setCurrentIndex((prev) => {
      if (prev >= totalActions - 1) {
        setIsPlaying(false);
        return prev;
      }
      return prev + 1;
    });
  }, [totalActions]);

  const goPrevious = useCallback(() => {
    setCurrentIndex((prev) => Math.max(-1, prev - 1));
    setIsPlaying(false);
  }, []);

  const togglePlay = useCallback(() => {
    setIsPlaying((prev) => {
      // If at the end, restart
      if (!prev) {
        setCurrentIndex((idx) => (idx >= totalActions - 1 ? -1 : idx));
      }
      return !prev;
    });
  }, [totalActions]);

  const handleSeek = useCallback((index: number) => {
    setCurrentIndex(index);
    setIsPlaying(false);
  }, []);

  const handleSetSpeed = useCallback((speed: number) => {
    setPlaybackSpeed(speed);
  }, []);

  // Auto-play interval
  useEffect(() => {
    if (isPlaying) {
      intervalRef.current = setInterval(() => {
        goNext();
      }, 1000 / playbackSpeed);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [isPlaying, playbackSpeed, goNext]);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't intercept keys when typing in form elements
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;

      if (e.key === 'ArrowRight') {
        e.preventDefault();
        goNext();
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        goPrevious();
      } else if (e.key === ' ') {
        e.preventDefault();
        togglePlay();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [goNext, goPrevious, togglePlay]);

  return (
    <div className="hand-replay-viewer">
      {/* Header info */}
      <div className="hand-replay-viewer__header">
        <span className="hand-replay-viewer__title">Hand #{data.hand_number}</span>
        {data.winners.length > 0 && (
          <div className="hand-replay-viewer__winners">
            {data.winners.map((w) => (
              <span key={w.name} className="hand-replay-viewer__winner">
                {w.name} wins ${w.amount_won.toLocaleString()}
                {w.hand_name && ` (${w.hand_name})`}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Main content area */}
      <div className="hand-replay-viewer__content">
        {/* Left: Table + controls */}
        <div className="hand-replay-viewer__main">
          <ReplayPokerTable visualState={visualState} />

          <TimelineScrubber
            actions={data.actions}
            currentIndex={currentIndex}
            onSeek={handleSeek}
          />

          <PlaybackControls
            currentIndex={currentIndex}
            totalActions={totalActions}
            isPlaying={isPlaying}
            playbackSpeed={playbackSpeed}
            actions={data.actions}
            onPrevious={goPrevious}
            onNext={goNext}
            onTogglePlay={togglePlay}
            onSetSpeed={handleSetSpeed}
            onSeek={handleSeek}
          />
        </div>

        {/* Right sidebar: Action log + Enrichment */}
        <div className="hand-replay-viewer__sidebar">
          <ActionLog actions={data.actions} currentIndex={currentIndex} />
          <EnrichmentPanel
            enrichment={currentEnrichment}
            playerName={visualState.current_player_name}
          />
        </div>
      </div>
    </div>
  );
}
