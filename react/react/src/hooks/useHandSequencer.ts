/**
 * useHandSequencer — the React driver for the pure `handSequencer` engine.
 *
 * Socket events are fed in via `enqueue*`; the driver runs ONE timer at a time,
 * draining the queue beat-by-beat. Each beat's plan (`planEvent`) gives the
 * effects to fire at their offsets plus the hold before the next event — so the
 * winner is always the last beat and never outruns the actions. The pacing tier
 * is read from the store at fire time, so toggling fast-forward mid-hand takes
 * effect on the next beat. See docs/plans/RUNOUT_PRESENTATION_SEQUENCER.md.
 *
 * It owns the queue, the engine state, the driving timers, the `isPlaying`
 * progress flag, and the hero card-commit gesture (`heroCommitted` /
 * `heroRetreating`). State application, the winner overlay, and the revealed
 * cards are owned by `usePokerGame`; the sequencer calls the injected setters
 * at the right beat.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useGameStore } from '../stores/gameStore';
import { avatarUrlForEmotion } from '../utils/avatarUrl';
import { deriveTier } from '../constants/presentationTiming';
import {
  planEvent,
  initialEngineState,
  type EngineState,
  type SourceEvent,
  type Effect,
} from './handSequencer';
import type { GameState, WinnerInfo, RevealedCardsInfo } from '../types/game';
import type { RunoutSchedule } from '../types/runout';

interface UseHandSequencerParams {
  /** Apply a game state (store + messages + AI-thinking). Stable. */
  applyState: (state: GameState) => void;
  /** Mount the revealed hole cards. Stable. */
  setReveal: (revealed: RevealedCardsInfo) => void;
  /** Mount the winner overlay. Stable. */
  setWinner: (winner: WinnerInfo) => void;
}

export interface HandSequencer {
  enqueueState: (state: GameState) => void;
  enqueueReveal: (revealed: RevealedCardsInfo) => void;
  enqueueWinner: (winner: WinnerInfo) => void;
  /** Store the per-card reaction schedule (data, resolved at fire time). */
  setSchedule: (schedule: RunoutSchedule | null) => void;
  /** Drop the queue + timers (reconnect / refresh) without replaying. */
  reset: () => void;
  /** True while any beat is pending — the "still going" progress signal. */
  isPlaying: boolean;
  /** Hero hole cards lifted to "present" at the all-in matchup reveal. */
  heroCommitted: boolean;
  /** Hero hole cards pulled back as the run-out board deals. */
  heroRetreating: boolean;
}

export function useHandSequencer({
  applyState,
  setReveal,
  setWinner,
}: UseHandSequencerParams): HandSequencer {
  const updatePlayers = useGameStore((s) => s.updatePlayers);
  const setRunoutDirectorActive = useGameStore((s) => s.setRunoutDirectorActive);
  const signalCardDeal = useGameStore((s) => s.signalCardDeal);

  const [isPlaying, setIsPlaying] = useState(false);
  const [heroCommitted, setHeroCommitted] = useState(false);
  const [heroRetreating, setHeroRetreating] = useState(false);

  const queueRef = useRef<SourceEvent[]>([]);
  const engineRef = useRef<EngineState>(initialEngineState);
  const effectTimersRef = useRef<number[]>([]);
  const pumpTimerRef = useRef<number | null>(null);
  const processingRef = useRef(false);
  const scheduleRef = useRef<RunoutSchedule | null>(null);

  // Effect callbacks read through refs so the driver (built once) always calls
  // the latest closures without re-subscribing.
  const applyStateRef = useRef(applyState);
  applyStateRef.current = applyState;
  const setRevealRef = useRef(setReveal);
  setRevealRef.current = setReveal;
  const setWinnerRef = useRef(setWinner);
  setWinnerRef.current = setWinner;

  const clearTimers = useCallback(() => {
    effectTimersRef.current.forEach((id) => clearTimeout(id));
    effectTimersRef.current = [];
    if (pumpTimerRef.current !== null) {
      clearTimeout(pumpTimerRef.current);
      pumpTimerRef.current = null;
    }
  }, []);

  const applyReaction = useCallback(
    (playerName: string, emotion: string) => {
      updatePlayers((prev) =>
        prev
          ? prev.map((p) =>
              p.name === playerName
                ? {
                    ...p,
                    avatar_emotion: emotion,
                    avatar_url: avatarUrlForEmotion(p.avatar_url, emotion),
                  }
                : p
            )
          : prev
      );
    },
    [updatePlayers]
  );

  const runEffect = useCallback(
    (effect: Effect) => {
      switch (effect.kind) {
        case 'applyState':
          applyStateRef.current(effect.state);
          break;
        case 'setReveal':
          setRevealRef.current(effect.revealed);
          break;
        case 'setWinner':
          setWinnerRef.current(effect.winner);
          break;
        case 'setActive':
          setRunoutDirectorActive(effect.active);
          break;
        case 'dealCards':
          signalCardDeal(effect.count, effect.total);
          break;
        case 'hero':
          if (effect.mode === 'commit') {
            setHeroCommitted(true);
            setHeroRetreating(false);
          } else if (effect.mode === 'retreat') {
            setHeroRetreating(true);
          } else {
            setHeroCommitted(false);
            setHeroRetreating(false);
          }
          break;
        case 'reactions': {
          const step = scheduleRef.current?.steps.find(
            (s) => s.phase === effect.phase && s.card_index === effect.cardIndex
          );
          step?.reactions.forEach((r) => applyReaction(r.player_name, r.emotion));
          break;
        }
        case 'recap':
          // Phase 4: a brief recap (via existing hand narrators) for the fastest
          // tier. No-op until then — the result still lands correctly.
          break;
      }
    },
    [applyReaction, setRunoutDirectorActive, signalCardDeal]
  );

  // Drain one event: plan it, fire its effects at their offsets, then schedule
  // the next pump after the beat's hold. Uses only refs, so it's stable.
  const pump = useCallback(() => {
    const next = queueRef.current.shift();
    if (!next) {
      processingRef.current = false;
      setIsPlaying(false);
      return;
    }
    const store = useGameStore.getState();
    const tier = deriveTier(store.fastForward, store.alwaysFastForward, store.aiInstant);
    const plan = planEvent(engineRef.current, next, tier);
    engineRef.current = plan.next;

    for (const te of plan.timeline) {
      if (te.at <= 0) {
        runEffect(te.effect);
      } else {
        effectTimersRef.current.push(window.setTimeout(() => runEffect(te.effect), te.at));
      }
    }
    pumpTimerRef.current = window.setTimeout(() => {
      pumpTimerRef.current = null;
      pump();
    }, plan.durationMs);
  }, [runEffect]);

  const enqueue = useCallback(
    (ev: SourceEvent) => {
      queueRef.current.push(ev);
      if (!processingRef.current) {
        processingRef.current = true;
        setIsPlaying(true);
        pump();
      }
    },
    [pump]
  );

  const enqueueState = useCallback(
    (state: GameState) => enqueue({ kind: 'state', state }),
    [enqueue]
  );
  const enqueueReveal = useCallback(
    (revealed: RevealedCardsInfo) => enqueue({ kind: 'reveal', revealed }),
    [enqueue]
  );
  const enqueueWinner = useCallback(
    (winner: WinnerInfo) => enqueue({ kind: 'winner', winner }),
    [enqueue]
  );
  const setSchedule = useCallback((schedule: RunoutSchedule | null) => {
    scheduleRef.current = schedule;
  }, []);

  const reset = useCallback(() => {
    clearTimers();
    queueRef.current = [];
    engineRef.current = initialEngineState;
    scheduleRef.current = null;
    processingRef.current = false;
    setIsPlaying(false);
    setHeroCommitted(false);
    setHeroRetreating(false);
    setRunoutDirectorActive(false);
  }, [clearTimers, setRunoutDirectorActive]);

  useEffect(() => () => clearTimers(), [clearTimers]);

  return {
    enqueueState,
    enqueueReveal,
    enqueueWinner,
    setSchedule,
    reset,
    isPlaying,
    heroCommitted,
    heroRetreating,
  };
}
