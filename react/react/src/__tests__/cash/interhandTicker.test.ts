import { describe, it, expect } from 'vitest';
import { selectInterhandTicker } from '../../components/cash/interhandTicker';
import type { LobbyEvent } from '../../components/cash/types';

/** Build a LobbyEvent with sane defaults; override what a test cares about. */
function ev(overrides: Partial<LobbyEvent> & { type: LobbyEvent['type'] }): LobbyEvent {
  return {
    table_id: 't1',
    stake_label: '$50',
    personality_id: overrides.personality_id ?? 'p1',
    name: overrides.name ?? 'Napoleon',
    reason: '',
    message: `${overrides.type} event`,
    created_at: '2026-05-26T12:00:00Z',
    ...overrides,
  };
}

describe('selectInterhandTicker', () => {
  it('drops comings/goings (join/leave) but keeps drama', () => {
    const out = selectInterhandTicker(
      [ev({ type: 'join' }), ev({ type: 'leave' }), ev({ type: 'bust', personality_id: 'p2' })],
      3
    );
    expect(out.map((e) => e.type)).toEqual(['bust']);
  });

  it('collapses a big_win/big_loss mirror pair into one line', () => {
    const out = selectInterhandTicker(
      [
        ev({ type: 'big_win', personality_id: 'winner', reason: 'loser', created_at: 'A' }),
        ev({ type: 'big_loss', personality_id: 'loser', reason: 'winner', created_at: 'A' }),
      ],
      5
    );
    expect(out).toHaveLength(1);
    expect(out[0].type).toBe('big_win');
  });

  it('orders rarer/bigger beats ahead of routine ones', () => {
    const out = selectInterhandTicker(
      [
        ev({ type: 'all_in', personality_id: 'a' }),
        ev({ type: 'big_win', personality_id: 'b' }),
        ev({ type: 'whale_arrival' as LobbyEvent['type'], personality_id: 'c' }),
      ],
      3
    );
    expect(out.map((e) => e.type)).toEqual(['whale_arrival', 'big_win', 'all_in']);
  });

  it('caps the digest at max', () => {
    const events = Array.from({ length: 6 }, (_, i) =>
      ev({ type: 'bust', personality_id: `p${i}`, created_at: `2026-05-26T12:0${i}:00Z` })
    );
    expect(selectInterhandTicker(events, 3)).toHaveLength(3);
  });

  it('shows the most recent first within a priority tier', () => {
    const out = selectInterhandTicker(
      [
        ev({ type: 'bust', personality_id: 'old', created_at: '2026-05-26T12:00:00Z' }),
        ev({ type: 'bust', personality_id: 'new', created_at: '2026-05-26T12:05:00Z' }),
      ],
      2
    );
    expect(out.map((e) => e.personality_id)).toEqual(['new', 'old']);
  });

  it('still shows an unknown/new event type (default priority, not dropped)', () => {
    const out = selectInterhandTicker(
      [ev({ type: 'some_future_event' as LobbyEvent['type'], personality_id: 'x' })],
      3
    );
    expect(out).toHaveLength(1);
  });

  it('de-dupes identical events by stable key', () => {
    const dup = ev({ type: 'last_stand', personality_id: 'p9', created_at: 'T' });
    const out = selectInterhandTicker([dup, { ...dup }], 5);
    expect(out).toHaveLength(1);
  });
});
