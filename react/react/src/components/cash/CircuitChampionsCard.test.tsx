/**
 * Tests for CircuitChampionsCard — the lobby Champions Roll. Verifies it stays
 * hidden until the circuit has a champion, shows only the latest champion when
 * collapsed, and expands on click to reveal the recent roll (champions, your own
 * finishes, and the "ran without you" markers).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

import { CircuitChampionsCard } from './CircuitChampionsCard';
import * as api from './tournamentApi';
import type { CircuitChampion } from './tournamentApi';

vi.mock('./avatarUrl', () => ({ avatarUrlForName: () => 'http://example/avatar.png' }));

function champ(over: Partial<CircuitChampion>): CircuitChampion {
  return {
    tournament_id: 't',
    winner_name: 'Mervin',
    field_size: 9,
    your_finish: null,
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

  it('collapsed by default: shows only the latest champion', async () => {
    vi.spyOn(api, 'getCircuitHistory').mockResolvedValue({
      events: [
        champ({ tournament_id: 't1', winner_name: 'Mervin' }), // latest
        champ({ tournament_id: 't2', winner_name: 'Napoleon' }),
      ],
    });
    render(<CircuitChampionsCard />);

    expect(await screen.findByText('Mervin')).toBeTruthy();
    expect(screen.getByText('Latest champion')).toBeTruthy();
    expect(screen.queryByText('Napoleon')).toBeNull(); // hidden until expanded
  });

  it('expands on click to reveal the roll with finishes and absent markers', async () => {
    vi.spyOn(api, 'getCircuitHistory').mockResolvedValue({
      events: [
        // latest: you played and lost → your finish shows
        champ({ tournament_id: 't1', winner_name: 'Mervin', played: true, your_finish: 4 }),
        // you played and won → "You" champion row, no redundant finish line
        champ({ tournament_id: 't2', winner_name: 'You', played: true, your_finish: 1 }),
        // ran without you
        champ({ tournament_id: 't3', winner_name: 'Napoleon', played: false }),
      ],
    });
    render(<CircuitChampionsCard />);

    await screen.findByText('Mervin');
    expect(screen.queryByText('You')).toBeNull(); // collapsed: rest hidden

    fireEvent.click(screen.getByRole('button')); // the toggle

    expect(await screen.findByText('You')).toBeTruthy();
    expect(screen.getByText('Napoleon')).toBeTruthy();
    expect(screen.getByText('Circuit champions')).toBeTruthy();
    expect(screen.getByText('you finished 4th')).toBeTruthy();
    expect(screen.queryByText('you finished 1st')).toBeNull();
    expect(screen.getByText('ran without you')).toBeTruthy();
  });
});
