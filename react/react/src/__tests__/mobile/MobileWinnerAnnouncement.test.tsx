import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { MobileWinnerAnnouncement } from '../../components/mobile/MobileWinnerAnnouncement';

// Mock the Card component to avoid canvas/image rendering issues
vi.mock('../../components/cards', () => ({
  Card: ({ card, size }: { card: unknown; size: string }) => (
    <div data-testid="mock-card" data-size={size}>
      {typeof card === 'string' ? card : JSON.stringify(card)}
    </div>
  ),
}));

// Mock the gameAPI to avoid real network calls
vi.mock('../../utils/api', () => ({
  gameAPI: {
    getPostRoundChatSuggestions: vi.fn().mockResolvedValue({
      suggestions: [
        { text: 'Nice hand!', tone: 'humble' },
        { text: 'Got lucky there.', tone: 'humble' },
      ],
    }),
  },
}));

// Mock logger
vi.mock('../../utils/logger', () => ({
  logger: {
    error: vi.fn(),
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
  },
}));

function makeShowdownWinnerInfo(overrides = {}) {
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
    community_cards: [
      { rank: 'Q', suit: 'spades' },
      { rank: 'J', suit: 'hearts' },
      { rank: '10', suit: 'diamonds' },
      { rank: '5', suit: 'clubs' },
      { rank: '2', suit: 'spades' },
    ],
    players_showdown: {
      Batman: {
        cards: [
          { rank: 'K', suit: 'spades' },
          { rank: 'K', suit: 'hearts' },
        ],
        hand_name: 'Pair of Kings',
        hand_rank: 2,
        kickers: ['A', 'Q'],
      },
      TestPlayer: {
        cards: [
          { rank: 'A', suit: 'spades' },
          { rank: 'K', suit: 'hearts' },
        ],
        hand_name: 'Ace High',
        hand_rank: 9,
        kickers: ['K', 'Q', 'J'],
      },
    },
    ...overrides,
  };
}

function makeFoldWinnerInfo(overrides = {}) {
  return {
    winners: ['Batman'],
    showdown: false,
    pot_breakdown: [
      {
        pot_name: 'Main Pot',
        total_amount: 150,
        winners: [{ name: 'Batman', amount: 150 }],
        hand_name: '',
      },
    ],
    ...overrides,
  };
}

function makeDefaultProps(overrides = {}) {
  return {
    winnerInfo: makeShowdownWinnerInfo(),
    onComplete: vi.fn(),
    gameId: 'test-game-123',
    playerName: 'TestPlayer',
    onSendMessage: vi.fn(),
    ...overrides,
  };
}

describe('VT-04: MobileWinnerAnnouncement — showdown vs fold display', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('Case 1: Showdown', () => {
    it('displays winner name and amount', () => {
      render(<MobileWinnerAnnouncement {...makeDefaultProps()} />);

      expect(screen.getByText('Batman wins')).toBeTruthy();
      expect(screen.getByText('$300')).toBeTruthy();
    });

    it('displays hand name', () => {
      render(<MobileWinnerAnnouncement {...makeDefaultProps()} />);

      // hand_name shown in winner header
      const handNames = screen.getAllByText('Pair of Kings');
      expect(handNames.length).toBeGreaterThanOrEqual(1);
    });

    it('displays showdown section with player hands after card timer', () => {
      render(<MobileWinnerAnnouncement {...makeDefaultProps()} />);

      // Cards hidden initially, shown after 800ms
      act(() => {
        vi.advanceTimersByTime(900);
      });

      const showdownSection = document.querySelector('.showdown-section');
      expect(showdownSection).toBeTruthy();

      // Player names in showdown
      const playerShowdowns = document.querySelectorAll('.player-showdown');
      expect(playerShowdowns.length).toBe(2);

      // Check player names appear in showdown
      expect(screen.getByText('Batman')).toBeTruthy();
      expect(screen.getByText('TestPlayer')).toBeTruthy();
    });

    it('displays community cards after card timer', () => {
      render(<MobileWinnerAnnouncement {...makeDefaultProps()} />);

      act(() => {
        vi.advanceTimersByTime(900);
      });

      const communitySection = document.querySelector('.community-section');
      expect(communitySection).toBeTruthy();

      // 5 community cards rendered
      const cards = communitySection!.querySelectorAll('[data-testid="mock-card"]');
      expect(cards.length).toBe(5);
    });

    it('shows Continue button', () => {
      render(<MobileWinnerAnnouncement {...makeDefaultProps()} />);

      const btn = screen.getByText('Continue');
      expect(btn).toBeTruthy();
    });
  });

  describe('Case 2: Fold (no showdown)', () => {
    it('displays winner name and amount', () => {
      render(
        <MobileWinnerAnnouncement
          {...makeDefaultProps({ winnerInfo: makeFoldWinnerInfo() })}
        />,
      );

      expect(screen.getByText('Batman')).toBeTruthy();
      expect(screen.getByText('Wins $150')).toBeTruthy();
    });

    it('shows "All opponents folded" text', () => {
      render(
        <MobileWinnerAnnouncement
          {...makeDefaultProps({ winnerInfo: makeFoldWinnerInfo() })}
        />,
      );

      expect(screen.getByText('All opponents folded')).toBeTruthy();
    });

    it('does not show showdown cards section', () => {
      render(
        <MobileWinnerAnnouncement
          {...makeDefaultProps({ winnerInfo: makeFoldWinnerInfo() })}
        />,
      );

      act(() => {
        vi.advanceTimersByTime(900);
      });

      expect(document.querySelector('.showdown-section')).toBeNull();
    });
  });

  describe('Case 3: Split pot', () => {
    it('displays multiple winner names with split text', () => {
      const splitWinnerInfo = makeShowdownWinnerInfo({
        winners: ['Batman', 'TestPlayer'],
        pot_breakdown: [
          {
            pot_name: 'Main Pot',
            total_amount: 300,
            winners: [
              { name: 'Batman', amount: 150 },
              { name: 'TestPlayer', amount: 150 },
            ],
            hand_name: 'Pair of Kings',
          },
        ],
      });

      render(
        <MobileWinnerAnnouncement
          {...makeDefaultProps({ winnerInfo: splitWinnerInfo })}
        />,
      );

      expect(screen.getByText('Batman & TestPlayer split')).toBeTruthy();
      expect(screen.getByText('$300')).toBeTruthy();
    });
  });

  describe('Case 4: Tournament final hand', () => {
    it('shows CHAMPION! banner when human won', () => {
      const finalHandInfo = makeShowdownWinnerInfo({
        is_final_hand: true,
        tournament_outcome: {
          human_won: true,
          human_position: 1,
        },
        winners: ['TestPlayer'],
        pot_breakdown: [
          {
            pot_name: 'Main Pot',
            total_amount: 500,
            winners: [{ name: 'TestPlayer', amount: 500 }],
            hand_name: 'Full House',
          },
        ],
      });

      render(
        <MobileWinnerAnnouncement
          {...makeDefaultProps({ winnerInfo: finalHandInfo })}
        />,
      );

      expect(screen.getByText('CHAMPION!')).toBeTruthy();
    });

    it('shows "Finished Xth" when human lost', () => {
      const finalHandInfo = makeFoldWinnerInfo({
        is_final_hand: true,
        tournament_outcome: {
          human_won: false,
          human_position: 2,
        },
      });

      render(
        <MobileWinnerAnnouncement
          {...makeDefaultProps({ winnerInfo: finalHandInfo })}
        />,
      );

      expect(screen.getByText('Finished 2nd')).toBeTruthy();
    });

    it('shows "Continue to Results" button for final hand', () => {
      const finalHandInfo = makeShowdownWinnerInfo({
        is_final_hand: true,
        tournament_outcome: {
          human_won: true,
          human_position: 1,
        },
      });

      render(
        <MobileWinnerAnnouncement
          {...makeDefaultProps({ winnerInfo: finalHandInfo })}
        />,
      );

      expect(screen.getByText('Continue to Results')).toBeTruthy();
    });

    it('does NOT auto-dismiss on final hand', () => {
      const onComplete = vi.fn();
      const finalHandInfo = makeShowdownWinnerInfo({
        is_final_hand: true,
        tournament_outcome: {
          human_won: true,
          human_position: 1,
        },
      });

      render(
        <MobileWinnerAnnouncement
          {...makeDefaultProps({ winnerInfo: finalHandInfo, onComplete })}
        />,
      );

      // Advance well past the auto-dismiss timer (12s for showdown)
      act(() => {
        vi.advanceTimersByTime(15000);
      });

      expect(onComplete).not.toHaveBeenCalled();
    });
  });

  describe('Auto-dismiss behavior', () => {
    it('auto-dismisses showdown after 12 seconds', () => {
      const onComplete = vi.fn();
      render(
        <MobileWinnerAnnouncement
          {...makeDefaultProps({ onComplete })}
        />,
      );

      act(() => {
        vi.advanceTimersByTime(12000);
      });

      expect(onComplete).toHaveBeenCalled();
    });

    it('auto-dismisses fold after 8 seconds', () => {
      const onComplete = vi.fn();
      render(
        <MobileWinnerAnnouncement
          {...makeDefaultProps({
            winnerInfo: makeFoldWinnerInfo(),
            onComplete,
          })}
        />,
      );

      act(() => {
        vi.advanceTimersByTime(8000);
      });

      expect(onComplete).toHaveBeenCalled();
    });
  });

  describe('Returns null when no winnerInfo', () => {
    it('renders nothing when winnerInfo is null', () => {
      const { container } = render(
        <MobileWinnerAnnouncement
          {...makeDefaultProps({ winnerInfo: null })}
        />,
      );

      expect(container.querySelector('.mobile-winner-overlay')).toBeNull();
    });
  });
});
