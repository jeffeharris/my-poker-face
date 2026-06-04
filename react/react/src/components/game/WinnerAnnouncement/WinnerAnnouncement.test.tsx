import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { WinnerAnnouncement } from './WinnerAnnouncement';

// Avoid canvas/image rendering from the real Card component
vi.mock('../../cards', () => ({
  Card: ({ card }: { card: unknown }) => (
    <div data-testid="mock-card">{typeof card === 'string' ? card : JSON.stringify(card)}</div>
  ),
}));

// No real network for suggestions
vi.mock('../../../utils/api', () => ({
  gameAPI: {
    getPostRoundChatSuggestions: vi.fn().mockResolvedValue({ suggestions: [] }),
  },
}));

const seat = (name: string, is_human: boolean) => ({
  name,
  stack: 1000,
  bet: 0,
  is_folded: false,
  is_all_in: false,
  is_human,
});

function showdownWinnerInfo(overrides = {}) {
  return {
    winners: ['Batman'],
    showdown: true,
    hand_name: 'Pair of Kings',
    pot_breakdown: [
      {
        pot_name: 'Main Pot',
        total_amount: 300,
        winners: [{ name: 'Batman', amount: 300 }],
        hand_name: 'Pair of Kings',
      },
    ],
    players_showdown: {
      Batman: { cards: [], hand_name: 'Pair of Kings', hand_rank: 2, kickers: [] },
      Robin: { cards: [], hand_name: 'Ace High', hand_rank: 9, kickers: [] },
    },
    ...overrides,
  };
}

describe('WinnerAnnouncement — unified situational tones', () => {
  beforeEach(() => vi.clearAllMocks());

  // Desktop now shares buildToneOptions with the mobile overlay: a winner at
  // showdown gets gloat/humble/gracious/props (the old static set had no
  // Gracious for winners).
  it('shows the situational WINNER tone set when the human seat won', () => {
    render(
      <WinnerAnnouncement
        winnerInfo={showdownWinnerInfo()}
        onComplete={vi.fn()}
        players={[seat('Batman', true), seat('Robin', false)]}
        gameId="g1"
        onSendMessage={vi.fn()}
      />
    );

    expect(screen.getByText('Gloat')).toBeTruthy();
    expect(screen.getByText('Gracious')).toBeTruthy();
    expect(screen.queryByText('Salty')).toBeNull();
  });

  // Regression parity with mobile: the win/loss read follows the is_human seat,
  // not the playerName prop (which can drift from the seat's player.name).
  it('follows the is_human seat, not playerName, for the win/loss read', () => {
    render(
      <WinnerAnnouncement
        winnerInfo={showdownWinnerInfo()}
        onComplete={vi.fn()}
        // Robin is the human and LOST; playerName stale-points at the winner.
        players={[seat('Batman', false), seat('Robin', true)]}
        gameId="g1"
        playerName="Batman"
        onSendMessage={vi.fn()}
      />
    );

    expect(screen.getByText('Salty')).toBeTruthy();
    expect(screen.queryByText('Gloat')).toBeNull();
  });
});
