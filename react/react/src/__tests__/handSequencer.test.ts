import { describe, it, expect } from 'vitest';
import {
  planEvent,
  initialEngineState,
  type EngineState,
  type Effect,
} from '../hooks/handSequencer';
import type { GameState, WinnerInfo, RevealedCardsInfo } from '../types/game';

// --- fixtures -------------------------------------------------------------

function gameState(over: Partial<GameState> = {}): GameState {
  return {
    players: [],
    community_cards: [],
    pot: { total: 0 },
    current_player_idx: 0,
    current_dealer_idx: 0,
    small_blind_idx: 0,
    big_blind_idx: 0,
    phase: 'PRE_FLOP',
    highest_bet: 0,
    player_options: [],
    min_raise: 0,
    big_blind: 0,
    small_blind: 0,
    hand_number: 1,
    messages: [],
    ...over,
  };
}

const cards = (n: number) => Array.from({ length: n }, (_, i) => `c${i}`);

const revealed: RevealedCardsInfo = { players_cards: {}, community_cards: [] };
const winner = (over: Partial<WinnerInfo> = {}): WinnerInfo => ({
  winners: ['Batman'],
  hand_name: 'Pair',
  showdown: true,
  ...over,
});

/** Pull the offsets of effects of a given kind from a plan timeline. */
const offsets = (plan: { timeline: { at: number; effect: Effect }[] }, kind: Effect['kind']) =>
  plan.timeline.filter((t) => t.effect.kind === kind).map((t) => t.at);

const runoutState = (over: Partial<EngineState> = {}): EngineState => ({
  ...initialEngineState,
  handNumber: 1,
  communityCount: 0,
  revealed: true,
  inRunout: true,
  ...over,
});

// --- action beat ----------------------------------------------------------

describe('planEvent — action (no new cards)', () => {
  it('applies state immediately and holds for the action beat, scaled by tier', () => {
    const ev = { kind: 'state', state: gameState() } as const;
    const base: EngineState = { ...initialEngineState, handNumber: 1 };

    expect(planEvent(base, ev, 'watchable').durationMs).toBe(1000);
    expect(planEvent(base, ev, 'fast').durationMs).toBe(400);
    expect(planEvent(base, ev, 'fastest').durationMs).toBe(0);

    const plan = planEvent(base, ev, 'watchable');
    expect(plan.timeline).toEqual([{ at: 0, effect: { kind: 'applyState', state: ev.state } }]);
  });
});

// --- deal beat ------------------------------------------------------------

describe('planEvent — community deal', () => {
  it('flop outside a run-out: gates the cascade, no reactions or hero gesture', () => {
    const g = gameState({ community_cards: cards(3), newly_dealt_count: 3 });
    const plan = planEvent(
      { ...initialEngineState, handNumber: 1 },
      { kind: 'state', state: g },
      'watchable'
    );
    expect(plan.durationMs).toBe(2825);
    expect(offsets(plan, 'reactions')).toEqual([]);
    expect(offsets(plan, 'hero')).toEqual([]);
    expect(offsets(plan, 'applyState')).toEqual([0]);
  });

  it('flop during a run-out: hero retreats and each card reacts on the cascade', () => {
    const g = gameState({ community_cards: cards(3), newly_dealt_count: 3 });
    const plan = planEvent(
      runoutState({ communityCount: 0 }),
      { kind: 'state', state: g },
      'watchable'
    );
    expect(plan.durationMs).toBe(2825);
    expect(offsets(plan, 'hero')).toEqual([0]);
    // card 0 @ 600, card 1 @ 1600, card 2 @ 2600
    expect(offsets(plan, 'reactions')).toEqual([600, 1600, 2600]);
    const reactions = plan.timeline.filter((t) => t.effect.kind === 'reactions');
    expect(reactions.map((t) => (t.effect as { cardIndex: number }).cardIndex)).toEqual([0, 1, 2]);
  });

  it('fast tier scales the per-card reaction offsets', () => {
    const g = gameState({ community_cards: cards(3), newly_dealt_count: 3 });
    const plan = planEvent(runoutState({ communityCount: 0 }), { kind: 'state', state: g }, 'fast');
    // 600/1600/2600 × 0.4
    expect(offsets(plan, 'reactions')).toEqual([240, 640, 1040]);
    expect(plan.durationMs).toBe(1130); // 2825 × 0.4
  });

  it('river during a run-out: showdown reactions, then release ownership; run-out ends', () => {
    const g = gameState({ community_cards: cards(5), newly_dealt_count: 1, hand_number: 1 });
    const plan = planEvent(
      runoutState({ communityCount: 4 }),
      { kind: 'state', state: g },
      'watchable'
    );
    // river card reaction @ 600, showdown @ 900, release @ 900 + 2500 = 3400
    expect(offsets(plan, 'reactions')).toEqual([600, 900]);
    expect(offsets(plan, 'setActive')).toEqual([3400]);
    expect(plan.durationMs).toBe(3400);
    expect(plan.next.inRunout).toBe(false);
  });
});

// --- reveal beat ----------------------------------------------------------

describe('planEvent — reveal (all-in matchup)', () => {
  it('reveals, claims reaction ownership, presents the hero hand, schedules INITIAL', () => {
    const plan = planEvent(
      { ...initialEngineState, handNumber: 1 },
      { kind: 'reveal', revealed },
      'watchable'
    );
    expect(offsets(plan, 'setReveal')).toEqual([0]);
    expect(
      plan.timeline.some((t) => t.effect.kind === 'setActive' && t.effect.active === true)
    ).toBe(true);
    expect(offsets(plan, 'hero')).toEqual([0]); // commit
    expect(offsets(plan, 'reactions')).toEqual([700]); // INITIAL
    expect(plan.durationMs).toBe(1500);
    expect(plan.next.revealed).toBe(true);
    expect(plan.next.inRunout).toBe(true);
  });

  it('a folded human does not present a hand (no hero commit)', () => {
    const plan = planEvent(
      { ...initialEngineState, handNumber: 1, heroFolded: true },
      { kind: 'reveal', revealed },
      'watchable'
    );
    expect(offsets(plan, 'hero')).toEqual([]);
    expect(offsets(plan, 'reactions')).toEqual([700]); // INITIAL still fires
  });
});

// --- winner beat ----------------------------------------------------------

describe('planEvent — winner (terminal beat)', () => {
  it('after a run-out: shows the verdict immediately (run-out already held)', () => {
    const plan = planEvent(
      runoutState({ inRunout: false }),
      { kind: 'winner', winner: winner() },
      'watchable'
    );
    expect(offsets(plan, 'setWinner')).toEqual([0]);
    expect(plan.durationMs).toBe(0);
  });

  it('hero-folded showdown with no run-out: a short breather before the verdict', () => {
    const plan = planEvent(
      { ...initialEngineState, handNumber: 1, heroFolded: true, revealed: false },
      { kind: 'winner', winner: winner() },
      'watchable'
    );
    expect(offsets(plan, 'setWinner')).toEqual([1500]);
    expect(plan.durationMs).toBe(1500);
  });

  it('fastest tier emits a recap alongside the result', () => {
    const plan = planEvent(
      { ...initialEngineState, handNumber: 1 },
      { kind: 'winner', winner: winner() },
      'fastest'
    );
    expect(offsets(plan, 'recap')).toEqual([0]);
    expect(offsets(plan, 'setWinner')).toEqual([0]);
  });
});

// --- hand reset -----------------------------------------------------------

describe('planEvent — new hand', () => {
  it('clears run-out flags when the hand number advances', () => {
    const g = gameState({ hand_number: 2 });
    const plan = planEvent(
      runoutState({ handNumber: 1 }),
      { kind: 'state', state: g },
      'watchable'
    );
    expect(plan.next.revealed).toBe(false);
    expect(plan.next.inRunout).toBe(false);
    expect(plan.next.handNumber).toBe(2);
  });
});
