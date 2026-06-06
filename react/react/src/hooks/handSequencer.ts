/**
 * handSequencer — the pure (no-React) core of the hand-presentation sequencer.
 *
 * The backend streams game signals as fast as it computes them; this engine
 * decides *when* each lands on screen and *what* derived beats (reactions, the
 * hero card gesture, the verdict) fire alongside, so the whole hand plays back as
 * one ordered, watchable timeline on one clock. The winner is the last beat — it
 * can never outrun the actions, by construction.
 *
 * It is deliberately framework-free: `planEvent` is a pure function of
 * (engine state, source event, tier). The driving timer, store writes, and the
 * live schedule lookup live in the `useHandSequencer` hook (Phase 2). See
 * docs/plans/RUNOUT_PRESENTATION_SEQUENCER.md.
 */
import type { GameState, WinnerInfo, RevealedCardsInfo } from '../types/game';
import { BEAT, scale, type PacingTier } from '../constants/presentationTiming';

/** A signal from the backend, fed to the engine in arrival order.
 *  (`runout_schedule` is data, not a beat — the hook stores it for reaction
 *  lookup at fire time, so it never needs to be a queued event here.) */
export type SourceEvent =
  | { kind: 'state'; state: GameState }
  | { kind: 'reveal'; revealed: RevealedCardsInfo }
  | { kind: 'winner'; winner: WinnerInfo };

/** A visual effect the hook executes against the store / overlays. Reaction
 *  *content* is resolved from the live schedule at fire time (robust to the
 *  reveal-then-schedule wire order); the engine only owns reaction *timing*. */
export type Effect =
  | { kind: 'applyState'; state: GameState }
  | { kind: 'setReveal'; revealed: RevealedCardsInfo }
  | { kind: 'hero'; mode: 'commit' | 'retreat' | 'idle' }
  | { kind: 'reactions'; phase: string; cardIndex: number }
  | { kind: 'setActive'; active: boolean }
  | { kind: 'setWinner'; winner: WinnerInfo }
  | { kind: 'recap'; winner: WinnerInfo };

/** One effect scheduled at an offset (ms) from the start of its event's beat. */
export interface TimedEffect {
  at: number;
  effect: Effect;
}

export interface EngineState {
  /** Hand the engine is currently presenting; a change resets run-out flags. */
  handNumber: number;
  /** Community cards already shown — baseline for detecting a new deal. */
  communityCount: number;
  /** Hole cards have been revealed this hand (i.e. an all-in run-out is on). */
  revealed: boolean;
  /** A run-out board is actively dealing (reveal seen, river not yet resolved). */
  inRunout: boolean;
  /** The human folded this hand (a spectator) — drives the hero gesture + verdict. */
  heroFolded: boolean;
}

export interface Plan {
  /** Effects to fire, each at its offset from the beat's start. */
  timeline: TimedEffect[];
  /** When the next queued event may begin (this beat's hold). */
  durationMs: number;
  /** The engine state after this event. */
  next: EngineState;
}

export const initialEngineState: EngineState = {
  handNumber: 0,
  communityCount: 0,
  revealed: false,
  inRunout: false,
  heroFolded: false,
};

function communityCount(s: GameState): number {
  return s.community_cards?.length ?? 0;
}

function heroFoldedOf(s: GameState): boolean {
  return s.players?.some((p) => p.is_human && p.is_folded) ?? false;
}

function phaseForCount(count: number): 'FLOP' | 'TURN' | 'RIVER' | null {
  if (count === 3) return 'FLOP';
  if (count === 4) return 'TURN';
  if (count === 5) return 'RIVER';
  return null;
}

/** Turn one source event into a timed plan. Pure: same inputs → same plan. */
export function planEvent(state: EngineState, event: SourceEvent, tier: PacingTier): Plan {
  switch (event.kind) {
    case 'state':
      return planState(state, event.state, tier);
    case 'reveal':
      return planReveal(state, event.revealed, tier);
    case 'winner':
      return planWinner(state, event.winner, tier);
  }
}

function planState(state: EngineState, g: GameState, tier: PacingTier): Plan {
  const newCount = communityCount(g);
  const handChanged = state.handNumber !== 0 && g.hand_number !== state.handNumber;
  const isDeal = (g.newly_dealt_count ?? 0) > 0 && newCount > state.communityCount && !handChanged;

  // A new hand clears run-out flags; its first state is just an apply.
  const base: EngineState = {
    handNumber: g.hand_number,
    communityCount: newCount,
    heroFolded: heroFoldedOf(g),
    revealed: handChanged ? false : state.revealed,
    inRunout: handChanged ? false : state.inRunout,
  };

  // A new hand drops any lingering hero card-commit gesture so the next run-out
  // starts from rest.
  const lead: TimedEffect[] = handChanged
    ? [{ at: 0, effect: { kind: 'hero', mode: 'idle' } }]
    : [];

  if (!isDeal) {
    return {
      timeline: [...lead, { at: 0, effect: { kind: 'applyState', state: g } }],
      durationMs: scale(BEAT.action, tier),
      next: base,
    };
  }

  const phase = phaseForCount(newCount);
  const newCards = newCount - state.communityCount; // 3 (flop) or 1 (turn/river)
  const timeline: TimedEffect[] = [...lead, { at: 0, effect: { kind: 'applyState', state: g } }];
  let duration = newCards === 3 ? scale(BEAT.flopGate, tier) : scale(BEAT.cardGate, tier);
  const next = { ...base };

  if (state.inRunout && phase) {
    // Pull the presented hero cards back so they don't cover the running board.
    timeline.push({ at: 0, effect: { kind: 'hero', mode: 'retreat' } });
    // Per-card reactions, aligned to the slide cascade.
    for (let i = 0; i < newCards; i++) {
      const at = scale(i * BEAT.perCardStagger + BEAT.reactionAfterCard, tier);
      timeline.push({ at, effect: { kind: 'reactions', phase, cardIndex: i } });
    }
    if (phase === 'RIVER') {
      const lastIdx = newCards - 1;
      const showdownAt = scale(lastIdx * BEAT.perCardStagger + BEAT.showdownReactionDelay, tier);
      timeline.push({
        at: showdownAt,
        effect: { kind: 'reactions', phase: 'SHOWDOWN', cardIndex: 0 },
      });
      // Stay authoritative through the lock-up, then release reaction ownership.
      const releaseAt = showdownAt + scale(BEAT.showdownHold, tier);
      timeline.push({ at: releaseAt, effect: { kind: 'setActive', active: false } });
      duration = Math.max(duration, releaseAt);
      next.inRunout = false; // run-out resolved after the river
    }
  }

  return { timeline, durationMs: duration, next };
}

function planReveal(state: EngineState, revealed: RevealedCardsInfo, tier: PacingTier): Plan {
  const timeline: TimedEffect[] = [
    { at: 0, effect: { kind: 'setReveal', revealed } },
    { at: 0, effect: { kind: 'setActive', active: true } },
  ];
  // A folded human is only spectating the AIs' showdown — no hand to present.
  if (!state.heroFolded) {
    timeline.push({ at: 0, effect: { kind: 'hero', mode: 'commit' } });
  }
  timeline.push({
    at: scale(BEAT.initialReactionDelay, tier),
    effect: { kind: 'reactions', phase: 'INITIAL', cardIndex: 0 },
  });

  return {
    timeline,
    durationMs: scale(BEAT.revealHold, tier),
    next: { ...state, revealed: true, inRunout: true },
  };
}

function planWinner(state: EngineState, winner: WinnerInfo, tier: PacingTier): Plan {
  // A hero-folded showdown that had NO run-out gets a short breather so the final
  // board lands before the verdict. An all-in run-out already held (the river beat
  // covers the showdown lock-up), so it goes straight to the result.
  const lead =
    winner.showdown && state.heroFolded && !state.revealed ? scale(BEAT.foldWatch, tier) : 0;
  const timeline: TimedEffect[] = [];
  if (tier === 'fastest') {
    timeline.push({ at: 0, effect: { kind: 'recap', winner } });
  }
  timeline.push({ at: lead, effect: { kind: 'setWinner', winner } });

  return { timeline, durationMs: lead, next: { ...state } };
}
