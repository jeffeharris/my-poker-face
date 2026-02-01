import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, waitFor } from '@testing-library/react';
import { HeadsUpOpponentPanel } from '../../components/mobile/HeadsUpOpponentPanel';
import type { Player } from '../../types';

// Mock framer-motion to avoid animation complexities in tests
vi.mock('framer-motion', () => ({
  motion: {
    div: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => {
      const { initial, animate, exit, transition, layout, ...htmlProps } = props;
      return <div {...htmlProps}>{children}</div>;
    },
  },
  AnimatePresence: ({ children }: React.PropsWithChildren<Record<string, unknown>>) => <>{children}</>,
}));

// Mock config
vi.mock('../../config', () => ({
  config: {
    API_URL: 'http://localhost:5000',
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

function makeOpponent(overrides: Partial<Player> = {}): Player {
  return {
    name: 'Batman',
    stack: 2000,
    bet: 0,
    is_folded: false,
    is_all_in: false,
    is_human: false,
    avatar_url: '/avatars/batman.png',
    nickname: 'The Dark Knight',
    psychology: {
      tilt_level: 0.0,
      tilt_category: 'none',
      losing_streak: 0,
    },
    ...overrides,
  };
}

function makePressureStatsResponse(opponentName: string, overrides: Record<string, unknown> = {}) {
  return {
    player_summaries: {
      [opponentName]: {
        total_events: 10,
        big_wins: 2,
        big_losses: 1,
        successful_bluffs: 3,
        bluffs_caught: 1,
        bad_beats: 0,
        eliminations: 1,
        biggest_pot_won: 500,
        biggest_pot_lost: 200,
        tilt_score: 0.1,
        aggression_score: 1.5,
        signature_move: 'Late-position steals',
        headsup_wins: 5,
        headsup_losses: 3,
        ...overrides,
      },
    },
  };
}

function makeMemoryDebugResponse(
  observerName: string,
  opponentName: string,
  overrides: Record<string, unknown> = {}
) {
  return {
    opponent_models: {
      [observerName]: {
        [opponentName]: {
          hands_observed: 8,
          vpip: 0.65,
          pfr: 0.3,
          aggression_factor: 2.1,
          play_style: 'loose-aggressive',
          summary: 'Plays many hands aggressively',
          ...overrides,
        },
      },
    },
  };
}

function mockFetchResponses(
  pressureStats: Record<string, unknown> | null,
  memoryDebug: Record<string, unknown> | null
) {
  global.fetch = vi.fn((url: string | URL | Request) => {
    const urlStr = typeof url === 'string' ? url : url.toString();
    if (urlStr.includes('/pressure-stats')) {
      return Promise.resolve({
        ok: pressureStats !== null,
        json: () => Promise.resolve(pressureStats || {}),
      } as Response);
    }
    if (urlStr.includes('/memory-debug')) {
      return Promise.resolve({
        ok: memoryDebug !== null,
        json: () => Promise.resolve(memoryDebug || {}),
      } as Response);
    }
    return Promise.resolve({ ok: false, json: () => Promise.resolve({}) } as Response);
  });
}

describe('VT-06: HeadsUpOpponentPanel — play style, tilt, record display', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  describe('Case 1: With psychology data', () => {
    it('shows "Reading [name]..." header', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman'),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent()}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const header = document.querySelector('.panel-header');
      expect(header).toBeTruthy();
      expect(header!.textContent).toContain('Reading');
      expect(header!.textContent).toContain('The Dark Knight');
    });

    it('shows play style label when enough hands observed', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman'),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent()}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const label = document.querySelector('.playstyle-label');
      expect(label).toBeTruthy();
      expect(label!.textContent).toBe('Loose & Aggressive');
    });

    it('shows VPIP and aggression stats', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman'),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent()}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const details = document.querySelector('.playstyle-details');
      expect(details).toBeTruthy();
      expect(details!.textContent).toContain('65% VPIP');
      expect(details!.textContent).toContain('Very aggressive');
    });

    it('shows hands observed count', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman'),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent()}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const handsObserved = document.querySelector('.hands-observed');
      expect(handsObserved).toBeTruthy();
      expect(handsObserved!.textContent).toContain('8 hands observed');
    });
  });

  describe('Case 2: With tilt', () => {
    it('shows tilt section with severity label for moderate tilt', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman'),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      const opponent = makeOpponent({
        psychology: {
          tilt_level: 0.6,
          tilt_category: 'moderate',
          tilt_source: 'bad_beat',
          losing_streak: 0,
        },
      });

      render(
        <HeadsUpOpponentPanel
          opponent={opponent}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const tiltSection = document.querySelector('.tilt-section');
      expect(tiltSection).toBeTruthy();
      expect(tiltSection!.classList.contains('moderate')).toBe(true);

      const tiltLabel = document.querySelector('.tilt-label');
      expect(tiltLabel).toBeTruthy();
      expect(tiltLabel!.textContent).toContain('Frustrated after bad beat');
    });

    it('shows tilt meter bar with correct width percentage', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman'),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      const opponent = makeOpponent({
        psychology: {
          tilt_level: 0.6,
          tilt_category: 'moderate',
          tilt_source: 'bad_beat',
          losing_streak: 0,
        },
      });

      render(
        <HeadsUpOpponentPanel
          opponent={opponent}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const meterFill = document.querySelector('.tilt-meter-fill') as HTMLElement;
      expect(meterFill).toBeTruthy();
      expect(meterFill.style.width).toBe('60%');
    });

    it('shows losing streak description for losing_streak tilt source', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman'),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      const opponent = makeOpponent({
        psychology: {
          tilt_level: 0.4,
          tilt_category: 'mild',
          tilt_source: 'losing_streak',
          losing_streak: 4,
        },
      });

      render(
        <HeadsUpOpponentPanel
          opponent={opponent}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const tiltLabel = document.querySelector('.tilt-label');
      expect(tiltLabel).toBeTruthy();
      expect(tiltLabel!.textContent).toContain('On a 4 hand losing streak');
    });
  });

  describe('Case 3: Calm state', () => {
    it('shows "Playing steady" text when no tilt and no emotional content', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman'),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      const opponent = makeOpponent({
        psychology: {
          tilt_level: 0.0,
          tilt_category: 'none',
          losing_streak: 0,
        },
      });

      render(
        <HeadsUpOpponentPanel
          opponent={opponent}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const calmSection = document.querySelector('.calm-section');
      expect(calmSection).toBeTruthy();
      const calmText = document.querySelector('.calm-text');
      expect(calmText).toBeTruthy();
      expect(calmText!.textContent).toBe('Playing steady');
    });

    it('does not show calm section when there is emotional content', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman'),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      const opponent = makeOpponent({
        psychology: {
          tilt_level: 0.0,
          tilt_category: 'none',
          losing_streak: 0,
          narrative: 'Feeling confident after bluff',
          inner_voice: 'I got this',
        },
      });

      render(
        <HeadsUpOpponentPanel
          opponent={opponent}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const calmSection = document.querySelector('.calm-section');
      expect(calmSection).toBeNull();

      // Emotional section should be shown instead
      const emotionalSection = document.querySelector('.emotional-section');
      expect(emotionalSection).toBeTruthy();
      expect(document.querySelector('.emotional-narrative')!.textContent).toContain('Feeling confident');
      expect(document.querySelector('.inner-voice')!.textContent).toContain('I got this');
    });
  });

  describe('Case 4: Heads-up record', () => {
    it('shows win/loss display', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman', { headsup_wins: 5, headsup_losses: 3 }),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent()}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const statsSection = document.querySelector('.stats-section');
      expect(statsSection).toBeTruthy();

      const wins = document.querySelector('.wins');
      expect(wins).toBeTruthy();
      expect(wins!.textContent).toBe('5W');

      const losses = document.querySelector('.losses');
      expect(losses).toBeTruthy();
      expect(losses!.textContent).toBe('3L');
    });

    it('shows best pot won', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman', { biggest_pot_won: 500 }),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent()}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const bestPot = document.querySelector('.biggest-pot');
      expect(bestPot).toBeTruthy();
      expect(bestPot!.textContent).toContain('$500');
    });

    it('shows signature move', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman', { signature_move: 'Late-position steals' }),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent()}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const signatureMove = document.querySelector('.signature-move');
      expect(signatureMove).toBeTruthy();
      expect(signatureMove!.textContent).toContain('Late-position steals');
    });

    it('shows 0W - 0L when no stats available', async () => {
      mockFetchResponses(null, null);

      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent()}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const wins = document.querySelector('.wins');
      expect(wins).toBeTruthy();
      expect(wins!.textContent).toBe('0W');

      const losses = document.querySelector('.losses');
      expect(losses).toBeTruthy();
      expect(losses!.textContent).toBe('0L');
    });
  });

  describe('Still reading state', () => {
    it('shows "Still reading..." when fewer than 3 hands observed', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman'),
        makeMemoryDebugResponse('TestPlayer', 'Batman', { hands_observed: 2 })
      );

      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent()}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const label = document.querySelector('.playstyle-label');
      expect(label).toBeTruthy();
      expect(label!.textContent).toBe('Still reading...');

      const handsObserved = document.querySelector('.hands-observed');
      expect(handsObserved).toBeTruthy();
      expect(handsObserved!.textContent).toContain('2 hands observed');
    });
  });

  describe('Polling behavior', () => {
    it('fetches data on 5-second interval', async () => {
      mockFetchResponses(
        makePressureStatsResponse('Batman'),
        makeMemoryDebugResponse('TestPlayer', 'Batman')
      );

      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent()}
          gameId="test-game"
          humanPlayerName="TestPlayer"
        />
      );

      // Initial fetch (2 calls: pressure-stats + memory-debug)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      const initialCallCount = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.length;
      expect(initialCallCount).toBe(2);

      // Advance 5 seconds for next poll
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });

      const afterPollCallCount = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.length;
      expect(afterPollCallCount).toBe(4); // 2 more calls
    });
  });
});
