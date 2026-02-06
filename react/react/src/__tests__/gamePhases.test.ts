import { describe, it, expect } from 'vitest';
import { isBettingPhase, NON_BETTING_PHASES } from '../constants/gamePhases';

describe('isBettingPhase', () => {
  const BETTING_PHASES = ['PRE_FLOP', 'FLOP', 'TURN', 'RIVER'];

  it('returns true for each betting phase', () => {
    for (const phase of BETTING_PHASES) {
      expect(isBettingPhase(phase, false)).toBe(true);
    }
  });

  it('returns false for each non-betting phase', () => {
    for (const phase of NON_BETTING_PHASES) {
      expect(isBettingPhase(phase, false)).toBe(false);
    }
  });

  it('returns false for undefined phase', () => {
    expect(isBettingPhase(undefined, false)).toBe(false);
  });

  it('returns false for null phase', () => {
    expect(isBettingPhase(null, false)).toBe(false);
  });

  it('returns false when runItOut is true even for betting phase', () => {
    expect(isBettingPhase('PRE_FLOP', true)).toBe(false);
    expect(isBettingPhase('FLOP', true)).toBe(false);
  });

  it('returns true when runItOut is undefined with a betting phase', () => {
    expect(isBettingPhase('PRE_FLOP', undefined)).toBe(true);
  });

  it('returns true when runItOut is false with a betting phase', () => {
    expect(isBettingPhase('RIVER', false)).toBe(true);
  });
});
