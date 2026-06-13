/**
 * Tests for CircuitChampionsCard — the lobby Champions Roll. Verifies it stays
 * hidden until the circuit has a champion, lists champions, highlights the
 * player's own titles ("You"), and flags the events that ran without them.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import { CircuitChampionsCard } from './CircuitChampionsCard';
import * as api from './tournamentApi';
import type { CircuitChampion } from './tournamentApi';

vi.mock('./avatarUrl', () => ({ avatarUrlForName: () => 'http://example/avatar.png' }));

function champ(over: Partial<CircuitChampion>): CircuitChampion {
  return {
    tournament_id: 't',
    winner_name: 'Mervin',
    field_size: 9,
    buy_in: 0,
    prize_pool: 0,
    completed_at: new Date().toISOString(),
    played: false,
    ...over,
  };
}

describe('CircuitChampionsCard', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders nothing until the circuit has crowned a champion', async () => {
    const spy = vi.spyOn(api, 'getCircuitHistory').mockResolvedValue({ events: [] });
    const { container } = render(<CircuitChampionsCard />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it('lists champions, flags passed events, and highlights your own titles', async () => {
    vi.spyOn(api, 'getCircuitHistory').mockResolvedValue({
      events: [
        champ({ tournament_id: 't1', winner_name: 'Mervin', played: false }),
        champ({ tournament_id: 't2', winner_name: 'You', field_size: 6, played: true }),
      ],
    });
    render(<CircuitChampionsCard />);

    expect(await screen.findByText('Mervin')).toBeTruthy();
    expect(screen.getByText('You')).toBeTruthy();
    // Only the event the player sat out carries the world-runs-without-you tag.
    expect(screen.getAllByText('ran without you')).toHaveLength(1);
  });
});
