import { describe, it, expect } from 'vitest';
import { arrivalSubtitle } from '../arrival';

/** Build a Date on a known weekday (2024-01-02 is a Tuesday) at the given
 *  local hour, so we test the time-of-day band boundaries precisely. */
function tuesdayAt(hour: number): Date {
  const d = new Date(2024, 0, 2, hour, 0, 0); // local time
  return d;
}

describe('arrivalSubtitle', () => {
  it('includes the weekday name', () => {
    expect(arrivalSubtitle(tuesdayAt(20))).toMatch(/^Tuesday /);
  });

  it.each([
    [0, 'late night'],
    [4, 'late night'],
    [5, 'morning'],
    [11, 'morning'],
    [12, 'afternoon'],
    [16, 'afternoon'],
    [17, 'evening'],
    [20, 'evening'],
    [21, 'night'],
    [23, 'night'],
  ])('hour %i → %s band', (hour, band) => {
    expect(arrivalSubtitle(tuesdayAt(hour))).toBe(`Tuesday ${band}`);
  });
});
