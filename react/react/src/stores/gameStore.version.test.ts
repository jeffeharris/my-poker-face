import { describe, it, expect, beforeEach } from 'vitest';
import { useGameStore } from './gameStore';

// Derive the store's types from the store itself (mirrors gameStore.optimistic.test.ts).
type State = ReturnType<typeof useGameStore.getState>;
type Player = NonNullable<State['players']>[number];

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

/** A minimal valid full-state push, tagged with `state_version` and `hand_number`. */
function frame(version: number | undefined, handNumber: number): GameStateArg {
  return {
    players: [
      mkPlayer({ name: 'Hero', is_human: true }),
      mkPlayer({ name: 'Villain', is_human: false }),
    ],
    phase: 'FLOP',
    pot: { total: 0 },
    community_cards: [],
    current_player_idx: 0,
    current_dealer_idx: 0,
    small_blind_idx: 0,
    big_blind_idx: 1,
    highest_bet: 0,
    player_options: [],
    min_raise: 0,
    big_blind: 100,
    small_blind: 50,
    hand_number: handNumber,
    messages: [],
    ...(version !== undefined ? { state_version: version } : {}),
  };
}

type GameStateArg = Parameters<State['applyGameState']>[0];

describe('applyGameState frame-version guard', () => {
  beforeEach(() => {
    useGameStore.getState().reset();
  });

  it('applies a newer socket frame and advances the version baseline', () => {
    useGameStore.getState().applyGameState(frame(5, 1));
    expect(useGameStore.getState().handNumber).toBe(1);
    expect(useGameStore.getState().stateVersion).toBe(5);

    useGameStore.getState().applyGameState(frame(6, 2));
    expect(useGameStore.getState().handNumber).toBe(2);
    expect(useGameStore.getState().stateVersion).toBe(6);
  });

  it('drops a stale socket frame (older version) — no regression to an earlier hand', () => {
    useGameStore.getState().applyGameState(frame(10, 2)); // applied hand 2
    // A leaked socket / late sequencer beat replays hand 1 at an older version.
    useGameStore.getState().applyGameState(frame(7, 1));
    expect(useGameStore.getState().handNumber).toBe(2); // unchanged
    expect(useGameStore.getState().stateVersion).toBe(10); // baseline unchanged
  });

  it('drops a duplicate (equal version) socket frame', () => {
    useGameStore.getState().applyGameState(frame(10, 2));
    useGameStore.getState().applyGameState(frame(10, 3)); // same version, newer hand payload
    expect(useGameStore.getState().handNumber).toBe(2); // duplicate ignored
  });

  it('authoritative refresh applies regardless of version and resets the baseline', () => {
    useGameStore.getState().applyGameState(frame(100, 5)); // socket got far ahead
    // A cold-load snapshot taken at a lower version (e.g. after a server restart
    // reset the global counter) must still apply — it is the source of truth.
    useGameStore.getState().applyGameState(frame(3, 9), true);
    expect(useGameStore.getState().handNumber).toBe(9);
    expect(useGameStore.getState().stateVersion).toBe(3);

    // Subsequent socket frames are then judged against the reset baseline.
    useGameStore.getState().applyGameState(frame(4, 10));
    expect(useGameStore.getState().handNumber).toBe(10);
  });

  it('never drops a frame from a server that omits state_version (back-compat)', () => {
    useGameStore.getState().applyGameState(frame(10, 2));
    useGameStore.getState().applyGameState(frame(undefined, 3)); // legacy server, no version
    expect(useGameStore.getState().handNumber).toBe(3);
    // Baseline is preserved (not clobbered) when the frame carries no version.
    expect(useGameStore.getState().stateVersion).toBe(10);
  });
});
