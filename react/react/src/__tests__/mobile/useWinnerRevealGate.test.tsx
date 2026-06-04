import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useWinnerRevealGate } from '../../hooks/useWinnerRevealGate';
import { RUNOUT_TIMING } from '../../constants/runoutTiming';

interface GateProps {
  hasWinner: boolean;
  isShowdown: boolean;
  handNumber: number;
  runItOut: boolean | undefined;
  heroFolded: boolean;
  runoutDirectorActive: boolean;
  rushing: boolean;
}

function scenario(overrides: Partial<GateProps> = {}) {
  const base: GateProps = {
    hasWinner: true,
    isShowdown: true,
    handNumber: 1,
    runItOut: false,
    heroFolded: false,
    runoutDirectorActive: false,
    rushing: false,
    ...overrides,
  };
  let props = base;
  const { rerender, result } = renderHook((p: GateProps) => useWinnerRevealGate(p), {
    initialProps: base,
  });
  return {
    hold: () => result.current.holdWinner,
    rerenderWith: (patch: Partial<GateProps>) => {
      props = { ...props, ...patch };
      act(() => rerender(props));
    },
  };
}

describe('useWinnerRevealGate', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it('does not hold when the hero played to showdown (not folded, not all-in)', () => {
    const s = scenario();
    expect(s.hold()).toBe(false);
  });

  it('does not hold a fold-out walk (no showdown)', () => {
    const s = scenario({ heroFolded: true, isShowdown: false });
    expect(s.hold()).toBe(false);
  });

  it('holds a folded-spectator showdown for the watch beat, then releases', () => {
    const s = scenario({ heroFolded: true });
    expect(s.hold()).toBe(true);
    act(() => vi.advanceTimersByTime(RUNOUT_TIMING.foldShowdownWatchMs - 1));
    expect(s.hold()).toBe(true);
    act(() => vi.advanceTimersByTime(1));
    expect(s.hold()).toBe(false);
  });

  it('drops the fold hold immediately when rushing (Skip via fast-forward)', () => {
    const s = scenario({ heroFolded: true });
    expect(s.hold()).toBe(true);
    s.rerenderWith({ rushing: true });
    expect(s.hold()).toBe(false);
  });

  it('holds an all-in run-out while the director owns the beat, releases on handoff', () => {
    const s = scenario({ runItOut: true, runoutDirectorActive: true });
    expect(s.hold()).toBe(true);
    s.rerenderWith({ runoutDirectorActive: false });
    expect(s.hold()).toBe(false);
  });

  it('reveals immediately for an all-in with no active director (no schedule)', () => {
    const s = scenario({ runItOut: true, runoutDirectorActive: false });
    expect(s.hold()).toBe(false);
  });

  it('never holds past the safety backstop', () => {
    const s = scenario({ runItOut: true, runoutDirectorActive: true });
    expect(s.hold()).toBe(true);
    act(() => vi.advanceTimersByTime(RUNOUT_TIMING.revealGateSafetyMs));
    expect(s.hold()).toBe(false);
  });

  it('re-arms the watch beat for the next hand', () => {
    const s = scenario({ heroFolded: true });
    act(() => vi.advanceTimersByTime(RUNOUT_TIMING.foldShowdownWatchMs));
    expect(s.hold()).toBe(false);
    // Next hand: winner clears, then a new folded showdown arrives.
    s.rerenderWith({ hasWinner: false });
    s.rerenderWith({ hasWinner: true, handNumber: 2 });
    expect(s.hold()).toBe(true);
    act(() => vi.advanceTimersByTime(RUNOUT_TIMING.foldShowdownWatchMs));
    expect(s.hold()).toBe(false);
  });
});
