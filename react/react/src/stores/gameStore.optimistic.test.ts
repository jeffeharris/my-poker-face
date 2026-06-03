import { describe, it, expect, beforeEach } from 'vitest';
import { useGameStore } from './gameStore';

// Derive the store's player/state types from the store itself, so the test
// needs no cross-directory type-only import (which vitest's transform fails
// to resolve for a types-only module).
type State = ReturnType<typeof useGameStore.getState>;
type Player = NonNullable<State['players']>[number];

/** Minimal valid Player; override per case. */
function mkPlayer(over: Partial<Player>): Player {
  return {
    name: 'p',
    stack: 1000,
    bet: 0,
    is_folded: false,
    is_all_in: false,
    is_human: true,
    ...over,
  };
}

/** Seed the store with a two-handed pot: human to act at idx 0. */
function seed(opts?: {
  humanBet?: number;
  villainBet?: number;
  highestBet?: number;
  pot?: number;
}) {
  const humanBet = opts?.humanBet ?? 0;
  const villainBet = opts?.villainBet ?? 0;
  const highestBet = opts?.highestBet ?? Math.max(humanBet, villainBet);
  const pot = opts?.pot ?? humanBet + villainBet;
  useGameStore.setState({
    players: [
      mkPlayer({ name: 'Hero', stack: 1000, bet: humanBet, is_human: true }),
      mkPlayer({ name: 'Villain', stack: 1000, bet: villainBet, is_human: false }),
    ],
    currentPlayerIdx: 0,
    highestBet,
    pot: { total: pot },
    optimisticSnapshot: null,
  });
}

beforeEach(() => {
  useGameStore.getState().reset();
});

describe('applyOptimisticAction', () => {
  it('call moves cost-to-call from stack to pot and matches highest bet', () => {
    seed({ humanBet: 0, villainBet: 100, pot: 100 });
    useGameStore.getState().applyOptimisticAction('call', undefined);

    const s = useGameStore.getState();
    expect(s.players![0].stack).toBe(900); // 1000 - 100
    expect(s.players![0].bet).toBe(100);
    expect(s.pot!.total).toBe(200); // 100 + 100
    expect(s.optimisticSnapshot).not.toBeNull();
  });

  it('raise TO an amount moves the top-up (raise_to - current bet)', () => {
    seed({ humanBet: 0, villainBet: 100, pot: 100 });
    // raise TO 300: top-up is 300 - 0 = 300
    useGameStore.getState().applyOptimisticAction('raise', 300);

    const s = useGameStore.getState();
    expect(s.players![0].stack).toBe(700);
    expect(s.players![0].bet).toBe(300);
    expect(s.pot!.total).toBe(400); // 100 + 300
    expect(s.highestBet).toBe(300);
  });

  it('a partial blind posted reduces the top-up on a raise', () => {
    // Hero already has 50 in front (e.g. small blind), raises TO 300.
    seed({ humanBet: 50, villainBet: 100, highestBet: 100, pot: 150 });
    useGameStore.getState().applyOptimisticAction('raise', 300);

    const s = useGameStore.getState();
    expect(s.players![0].stack).toBe(750); // 1000 - (300 - 50)
    expect(s.players![0].bet).toBe(300);
    expect(s.pot!.total).toBe(400); // 150 + 250
  });

  it('clamps to the stack and flags all-in when the delta exceeds chips', () => {
    seed({ humanBet: 0, villainBet: 5000, pot: 5000 });
    // call would cost 5000 but Hero only has 1000
    useGameStore.getState().applyOptimisticAction('call', undefined);

    const s = useGameStore.getState();
    expect(s.players![0].stack).toBe(0);
    expect(s.players![0].bet).toBe(1000);
    expect(s.players![0].is_all_in).toBe(true);
    expect(s.pot!.total).toBe(6000); // 5000 + 1000
  });

  it('check moves nothing and records no snapshot', () => {
    seed({ humanBet: 0, villainBet: 0, pot: 0 });
    useGameStore.getState().applyOptimisticAction('check', undefined);

    const s = useGameStore.getState();
    expect(s.players![0].stack).toBe(1000);
    expect(s.pot!.total).toBe(0);
    expect(s.optimisticSnapshot).toBeNull();
  });

  it('fold moves nothing and records no snapshot', () => {
    seed({ humanBet: 0, villainBet: 100, pot: 100 });
    useGameStore.getState().applyOptimisticAction('fold', undefined);

    const s = useGameStore.getState();
    expect(s.players![0].stack).toBe(1000);
    expect(s.pot!.total).toBe(100);
    expect(s.optimisticSnapshot).toBeNull();
  });
});

describe('rollbackOptimisticAction', () => {
  it('restores the pre-action stack, bet, pot, and highest bet', () => {
    seed({ humanBet: 0, villainBet: 100, pot: 100 });
    useGameStore.getState().applyOptimisticAction('raise', 300);
    useGameStore.getState().rollbackOptimisticAction();

    const s = useGameStore.getState();
    expect(s.players![0].stack).toBe(1000);
    expect(s.players![0].bet).toBe(0);
    expect(s.pot!.total).toBe(100);
    expect(s.highestBet).toBe(100);
    expect(s.optimisticSnapshot).toBeNull();
  });

  it('is a no-op when there is no pending snapshot', () => {
    seed({ humanBet: 0, villainBet: 100, pot: 100 });
    useGameStore.getState().rollbackOptimisticAction();

    const s = useGameStore.getState();
    expect(s.players![0].stack).toBe(1000);
    expect(s.pot!.total).toBe(100);
  });
});

describe('applyGameState reconciliation', () => {
  it('clears the optimistic snapshot when authoritative state arrives', () => {
    seed({ humanBet: 0, villainBet: 100, pot: 100 });
    useGameStore.getState().applyOptimisticAction('call', undefined);
    expect(useGameStore.getState().optimisticSnapshot).not.toBeNull();

    // Minimal authoritative push.
    const authoritative: Parameters<State['applyGameState']>[0] = {
      players: [
        mkPlayer({ name: 'Hero', stack: 900, bet: 100, is_human: true }),
        mkPlayer({ name: 'Villain', stack: 900, bet: 100, is_human: false }),
      ],
      phase: 'FLOP',
      pot: { total: 200 },
      community_cards: [],
      current_player_idx: 1,
      current_dealer_idx: 0,
      small_blind_idx: 0,
      big_blind_idx: 1,
      highest_bet: 100,
      player_options: [],
      min_raise: 100,
      big_blind: 100,
      small_blind: 50,
      hand_number: 1,
      messages: [],
    };
    useGameStore.getState().applyGameState(authoritative);

    expect(useGameStore.getState().optimisticSnapshot).toBeNull();
  });
});
