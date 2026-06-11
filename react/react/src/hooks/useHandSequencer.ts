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
import { HAPTICS, startHeartbeat, stopHeartbeat } from '../utils/haptics';
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
  /** Queue a game-state beat. `commentary` = this push carries new AI table talk,
   *  so a sped-up watchable beat is floored to leave reading time. */
  enqueueState: (state: GameState, commentary?: boolean) => void;
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
  // Haptics: track each opponent's last broadcast action so we buzz once when an
  // AI commits chips (raise/all-in), not on every re-render or non-acting beat.
  // Keyed by name → `${last_action}@${stack}`; the stack drop makes each distinct
  // commit re-fire while a stale action sitting through other beats stays silent.
  const oppActionSigRef = useRef<Map<string, string>>(new Map());
  const handNoRef = useRef<number | null>(null);
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

  // Buzz once per opponent action so you can feel the action go around the table
  // without looking: a double-knock on a check/call, an increasing-intensity ramp
  // on a raise, the big crescendo+buzz on an all-in. Folds stay silent (a fold is
  // a non-event tactilely, and keeps the signal from getting noisy).
  const signalOpponentAction = useCallback((state: GameState) => {
    // New hand → forget last hand's actions so the first action re-fires.
    if (handNoRef.current !== state.hand_number) {
      handNoRef.current = state.hand_number;
      oppActionSigRef.current.clear();
    }
    for (const p of state.players ?? []) {
      if (p.is_human) continue;
      const action = p.last_action;
      if (action !== 'raise' && action !== 'all_in' && action !== 'check' && action !== 'call')
        continue;
      // Include phase so a check/call that repeats across streets (same action,
      // unchanged stack) still re-knocks each street instead of deduping away.
      const sig = `${action}@${p.stack}@${state.phase}`;
      if (oppActionSigRef.current.get(p.name) === sig) continue;
      oppActionSigRef.current.set(p.name, sig);
      if (action === 'all_in') HAPTICS.allIn();
      else if (action === 'raise') HAPTICS.opponentRaise();
      else HAPTICS.knock(); // check or call → a plain double knock
    }
  }, []);

  const runEffect = useCallback(
    (effect: Effect) => {
      switch (effect.kind) {
        case 'applyState':
          applyStateRef.current(effect.state);
          signalOpponentAction(effect.state);
          break;
        case 'setReveal':
          setRevealRef.current(effect.revealed);
          // All-in showdown: the board is about to run out with chips committed.
          // If YOU'RE in it (your cards are among those revealed), start a
          // heartbeat that beats through the sweat until the winner lands.
          {
            const humanName = useGameStore.getState().players?.find((p) => p.is_human)?.name;
            if (humanName && effect.revealed?.players_cards?.[humanName]) {
              startHeartbeat();
            }
          }
          break;
        case 'setWinner':
          stopHeartbeat(); // the sweat is over — kill the heartbeat before the verdict
          setWinnerRef.current(effect.winner);
          break;
        case 'setActive':
          setRunoutDirectorActive(effect.active);
          break;
        case 'dealCards':
          signalCardDeal(effect.count, effect.total);
          // A soft tick as the board advances (flop/turn/river) so you can feel
          // the street change without watching. Native-only no-op on web.
          HAPTICS.boardCard();
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
    [applyReaction, setRunoutDirectorActive, signalCardDeal, signalOpponentAction]
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
    (state: GameState, commentary = false) => enqueue({ kind: 'state', state, commentary }),
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
    oppActionSigRef.current.clear();
    handNoRef.current = null;
    stopHeartbeat(); // never let a heartbeat outlive a dropped/replayed hand
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
