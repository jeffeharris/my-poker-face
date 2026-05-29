import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useRunoutDirector } from '../../hooks/useRunoutDirector';
import { RUNOUT_TIMING } from '../../constants/runoutTiming';
import type { RunoutSchedule } from '../../types/runout';

function makeSchedule(): RunoutSchedule {
  return {
    steps: [
      { phase: 'INITIAL', card_index: 0, reactions: [{ player_name: 'A', emotion: 'smug' }] },
      { phase: 'FLOP', card_index: 0, reactions: [{ player_name: 'A', emotion: 'angry' }] },
      { phase: 'FLOP', card_index: 1, reactions: [{ player_name: 'A', emotion: 'elated' }] },
      { phase: 'FLOP', card_index: 2, reactions: [] }, // a card that moved nobody
      { phase: 'TURN', card_index: 0, reactions: [{ player_name: 'A', emotion: 'happy' }] },
      { phase: 'RIVER', card_index: 0, reactions: [{ player_name: 'A', emotion: 'frustrated' }] },
      { phase: 'SHOWDOWN', card_index: 0, reactions: [{ player_name: 'A', emotion: 'elated' }] },
    ],
  };
}

const T = RUNOUT_TIMING;

interface ScenarioState {
  applied: Array<[string, string]>;
  active: boolean[];
  rerenderWith: (patch: Partial<DirectorProps>) => void;
  heroCommitted: () => boolean;
  heroRetreating: () => boolean;
}

interface DirectorProps {
  schedule: RunoutSchedule | null;
  runItOut: boolean | undefined;
  revealed: boolean;
  communityCardCount: number;
  handNumber: number;
  fastForward: boolean;
}

/** Render the director with stable applyReaction/setActive spies and a stable
 *  schedule object (so rerenders don't look like a brand-new run-out). */
function scenario(overrides: Partial<DirectorProps> = {}): ScenarioState {
  const applied: Array<[string, string]> = [];
  const active: boolean[] = [];
  const applyReaction = (name: string, emotion: string) => applied.push([name, emotion]);
  const setActive = (a: boolean) => active.push(a);

  const base: DirectorProps = {
    schedule: makeSchedule(),
    runItOut: true,
    revealed: false,
    communityCardCount: 0,
    handNumber: 1,
    fastForward: false,
    ...overrides,
  };
  let props = base;

  const { rerender, result } = renderHook(
    (p: DirectorProps) => useRunoutDirector({ ...p, applyReaction, setActive }),
    { initialProps: base }
  );

  return {
    applied,
    active,
    rerenderWith: (patch) => {
      props = { ...props, ...patch };
      act(() => rerender(props));
    },
    heroCommitted: () => result.current.heroCommitted,
    heroRetreating: () => result.current.heroRetreating,
  };
}

describe('useRunoutDirector', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('claims reaction ownership when a run-out schedule arrives', () => {
    const s = scenario();
    expect(s.active.at(-1)).toBe(true);
  });

  it('does not claim ownership when there is no schedule', () => {
    const s = scenario({ schedule: null });
    expect(s.active).toEqual([]);
  });

  it('fires the matchup (INITIAL) read a beat after the reveal', () => {
    const s = scenario();
    s.rerenderWith({ revealed: true });
    act(() => vi.advanceTimersByTime(T.initialReactionDelayMs - 1));
    expect(s.applied).toEqual([]);
    act(() => vi.advanceTimersByTime(1));
    expect(s.applied).toEqual([['A', 'smug']]);
  });

  it('plays each flop card as its own staggered beat', () => {
    const s = scenario();
    s.rerenderWith({ revealed: true });
    act(() => vi.advanceTimersByTime(T.initialReactionDelayMs));
    expect(s.applied).toEqual([['A', 'smug']]);

    // Flop lands (3 cards)
    s.rerenderWith({ communityCardCount: 3 });
    act(() => vi.advanceTimersByTime(T.reactionAfterCardMs)); // card 0
    expect(s.applied.at(-1)).toEqual(['A', 'angry']);
    act(() => vi.advanceTimersByTime(T.perCardStaggerMs)); // card 1
    expect(s.applied.at(-1)).toEqual(['A', 'elated']);
    act(() => vi.advanceTimersByTime(T.perCardStaggerMs)); // card 2 — empty, no new face
    expect(s.applied).toEqual([
      ['A', 'smug'],
      ['A', 'angry'],
      ['A', 'elated'],
    ]);
  });

  it('plays turn, then river, then the showdown lock-up, and releases ownership', () => {
    const s = scenario();
    s.rerenderWith({ revealed: true, communityCardCount: 3 });
    act(() => vi.advanceTimersByTime(10_000)); // drain reveal + flop
    s.applied.length = 0;

    s.rerenderWith({ communityCardCount: 4 }); // turn
    act(() => vi.advanceTimersByTime(T.reactionAfterCardMs));
    expect(s.applied.at(-1)).toEqual(['A', 'happy']);

    s.rerenderWith({ communityCardCount: 5 }); // river
    act(() => vi.advanceTimersByTime(T.reactionAfterCardMs));
    expect(s.applied.at(-1)).toEqual(['A', 'frustrated']);
    expect(s.active.at(-1)).toBe(true);

    // Showdown lock-up fires, but ownership is HELD so the next state push can't
    // revert the face instantly.
    act(() => vi.advanceTimersByTime(T.showdownReactionDelayMs - T.reactionAfterCardMs));
    expect(s.applied.at(-1)).toEqual(['A', 'elated']);
    expect(s.active.at(-1)).toBe(true);

    // ...then released after the hold.
    act(() => vi.advanceTimersByTime(T.showdownHoldMs));
    expect(s.active.at(-1)).toBe(false);
  });

  it('compresses every beat under fast-forward', () => {
    const s = scenario({ fastForward: true });
    s.rerenderWith({ revealed: true });
    // 10% of initialReactionDelayMs, minus a tick, should not be enough
    act(() => vi.advanceTimersByTime(Math.round(T.initialReactionDelayMs * T.ffMultiplier) - 1));
    expect(s.applied).toEqual([]);
    act(() => vi.advanceTimersByTime(1));
    expect(s.applied).toEqual([['A', 'smug']]);
  });

  it('releases ownership via the safety cap if the board stalls', () => {
    const s = scenario();
    expect(s.active.at(-1)).toBe(true);
    // No cards ever arrive; the cap must still let go.
    act(() => vi.advanceTimersByTime(T.safetyCapMs));
    expect(s.active.at(-1)).toBe(false);
  });

  it('presents the hero cards on reveal, holds, retreats when the run-out deals, resets on resolve', () => {
    const s = scenario();
    // Not committed before the matchup is revealed.
    expect(s.heroCommitted()).toBe(false);
    expect(s.heroRetreating()).toBe(false);
    // Revealed → hero cards present (held up), not yet retreating.
    s.rerenderWith({ revealed: true });
    expect(s.heroCommitted()).toBe(true);
    expect(s.heroRetreating()).toBe(false);
    // First run-out card deals → cards pull back down.
    s.rerenderWith({ communityCardCount: 3 });
    expect(s.heroRetreating()).toBe(true);
    // Run-out resolves (run_it_out clears) → both reset.
    s.rerenderWith({ runItOut: false });
    expect(s.heroCommitted()).toBe(false);
    expect(s.heroRetreating()).toBe(false);
  });

  it('ignores cards already on the board when the run-out starts post-flop', () => {
    // All-in on the flop: board already shows 3, no FLOP steps should fire.
    const s = scenario({ communityCardCount: 3 });
    s.rerenderWith({ revealed: true });
    act(() => vi.advanceTimersByTime(10_000));
    // INITIAL fired, but no flop card reactions (the flop predates the run-out)
    expect(s.applied).toEqual([['A', 'smug']]);
  });
});
