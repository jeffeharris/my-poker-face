/**
 * RelationshipMatrix — rendering tests.
 *
 * The component is pure presentation over the relationships JSON payload;
 * these tests lock the contract:
 *   - matrix cells render for every (observer, opponent) pair
 *   - label classes propagate to cell elements
 *   - clicking a cell surfaces the detail panel with memorable hands
 *   - empty payload renders a "no data" placeholder
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {
  RelationshipMatrix,
  type RelationshipsPayload,
} from '../../components/admin/RelationshipMatrix';

function makePayload(overrides: Partial<RelationshipsPayload> = {}): RelationshipsPayload {
  return {
    game_id: 'test',
    pair_count: 2,
    now: '2026-05-20T12:00:00',
    pairs: [
      {
        observer: 'alice',
        opponent: 'bob',
        observer_id: 'alice_pid',
        opponent_id: 'bob_pid',
        heat: 0.65,
        respect: 0.4,
        likability: 0.5,
        label: 'rival',
        last_seen: '2026-05-20T11:00:00',
        memorable_hands: [
          {
            hand_id: 42,
            event: 'bad_beat',
            impact_score: 0.9,
            narrative: 'bob bad-beat alice on hand 42',
            timestamp: '2026-05-20T10:30:00',
          },
        ],
      },
      {
        observer: 'bob',
        opponent: 'alice',
        observer_id: 'bob_pid',
        opponent_id: 'alice_pid',
        heat: 0.0,
        respect: 0.8,
        likability: 0.85,
        label: 'friendly',
        last_seen: '2026-05-20T11:00:00',
        memorable_hands: [],
      },
    ],
    ...overrides,
  };
}

describe('RelationshipMatrix', () => {
  it('renders a row for each player and a header row', () => {
    const { container } = render(<RelationshipMatrix data={makePayload()} />);
    // 1 header row + 2 data rows (alice, bob)
    const rows = container.querySelectorAll('tr');
    expect(rows.length).toBe(3);
  });

  it('applies the label class to rival and friendly cells', () => {
    const { container } = render(<RelationshipMatrix data={makePayload()} />);
    const rivalCell = container.querySelector('.rmx-cell-rival');
    const friendlyCell = container.querySelector('.rmx-cell-friendly');
    expect(rivalCell).toBeTruthy();
    expect(friendlyCell).toBeTruthy();
  });

  it('renders a self-pair cell for the diagonal (no click)', () => {
    const { container } = render(<RelationshipMatrix data={makePayload()} />);
    const selfCells = container.querySelectorAll('.rmx-self');
    // Two players → two diagonal cells.
    expect(selfCells.length).toBe(2);
  });

  it('does not show detail panel until a cell is clicked', () => {
    render(<RelationshipMatrix data={makePayload()} />);
    expect(screen.queryByTestId('rmx-detail')).toBeNull();
  });

  it('shows detail panel with memorable hands when rival cell is clicked', () => {
    const { container } = render(<RelationshipMatrix data={makePayload()} />);
    const rivalCell = container.querySelector('.rmx-cell-rival') as HTMLElement;
    fireEvent.click(rivalCell);
    const panel = screen.getByTestId('rmx-detail');
    expect(panel).toBeTruthy();
    expect(panel.textContent).toContain('bad-beat alice on hand 42');
  });

  it('shows neutral label and empty memorable-hands when friendly cell with no hands is clicked', () => {
    const { container } = render(<RelationshipMatrix data={makePayload()} />);
    const friendlyCell = container.querySelector('.rmx-cell-friendly') as HTMLElement;
    fireEvent.click(friendlyCell);
    const panel = screen.getByTestId('rmx-detail');
    expect(panel.textContent).toContain('None yet.');
  });

  it('renders empty-state placeholder when payload has no pairs', () => {
    const empty: RelationshipsPayload = {
      game_id: 'empty',
      pair_count: 0,
      now: '2026-05-20T12:00:00',
      pairs: [],
    };
    render(<RelationshipMatrix data={empty} />);
    expect(screen.getByText(/No relationship data yet/i)).toBeTruthy();
  });
});
