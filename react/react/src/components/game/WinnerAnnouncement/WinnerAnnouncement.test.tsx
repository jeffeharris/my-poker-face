import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { WinnerAnnouncement } from './WinnerAnnouncement';
import { gameAPI } from '../../../utils/api';

// Avoid canvas/image rendering from the real Card component
vi.mock('../../cards', () => ({
  Card: ({ card }: { card: unknown }) => (
    <div data-testid="mock-card">{typeof card === 'string' ? card : JSON.stringify(card)}</div>
  ),
}));

// No real network for suggestions
vi.mock('../../../utils/api', () => ({
  gameAPI: {
    getPostRoundChatSuggestions: vi.fn(),
  },
}));

const mockGetSuggestions = vi.mocked(gameAPI.getPostRoundChatSuggestions);

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
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSuggestions.mockResolvedValue({
      suggestions: [{ text: 'Nice hand.', tone: 'gracious' }],
    });
  });

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

describe('WinnerAnnouncement — sarcastic register (parity with mobile)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSuggestions.mockResolvedValue({
      suggestions: [{ text: 'Nice hand.', tone: 'gracious' }],
    });
  });

  const renderWinner = () =>
    render(
      <WinnerAnnouncement
        winnerInfo={showdownWinnerInfo()}
        onComplete={vi.fn()}
        players={[seat('Batman', true), seat('Robin', false)]}
        gameId="g1"
        onSendMessage={vi.fn()}
      />
    );

  it('offers the Sarcastic toggle on a sarcasm-able tone (Gracious)', async () => {
    renderWinner();
    fireEvent.click(screen.getByText('Gracious'));

    // Once suggestions land, the toggle appears in the suggestions bar.
    await screen.findByText('Nice hand.');
    expect(screen.queryByText(/Sarcastic/)).not.toBeNull();
  });

  it('hides the Sarcastic toggle on a non-sarcasm-able tone (Gloat)', async () => {
    renderWinner();
    fireEvent.click(screen.getByText('Gloat'));

    await screen.findByText('Nice hand.');
    expect(screen.queryByText(/Sarcastic/)).toBeNull();
  });

  it('refetches with sarcastic intensity when the toggle is turned on', async () => {
    renderWinner();
    fireEvent.click(screen.getByText('Gracious'));
    await screen.findByText('Nice hand.');

    // Initial fetch is sincere (no intensity).
    expect(mockGetSuggestions).toHaveBeenLastCalledWith('g1', 'Batman', 'gracious', undefined);

    fireEvent.click(screen.getByText(/Sarcastic/));
    expect(mockGetSuggestions).toHaveBeenLastCalledWith('g1', 'Batman', 'gracious', 'sarcastic');
  });
});
