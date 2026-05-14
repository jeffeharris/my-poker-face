import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import { HeadsUpOpponentPanel } from '../../components/mobile/HeadsUpOpponentPanel';
import type { Player, OpponentObservation, PlayerPressureSummary } from '../../types';

// Mock framer-motion to avoid animation complexities in tests
vi.mock('framer-motion', () => ({
  motion: {
    div: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => {
      const { initial: _initial, animate: _animate, exit: _exit, transition: _transition, layout: _layout, ...htmlProps } = props;
      return <div {...htmlProps}>{children}</div>;
    },
  },
  AnimatePresence: ({ children }: React.PropsWithChildren<Record<string, unknown>>) => <>{children}</>,
}));

function makeObservation(overrides: Partial<OpponentObservation> = {}): OpponentObservation {
  return {
    hands_observed: 10,
    vpip: 0.65,
    pfr: 0.3,
    aggression_factor: 2.1,
    play_style: 'loose-aggressive',
    ...overrides,
  };
}

function makePressureSummary(overrides: Partial<PlayerPressureSummary> = {}): PlayerPressureSummary {
  return {
    total_events: 10,
    wins: 5,
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
  };
}

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
    observation: makeObservation(),
    pressure_summary: makePressureSummary(),
    ...overrides,
  };
}

describe('VT-06: HeadsUpOpponentPanel — play style, tilt, record display', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('Case 1: With observation data', () => {
    it('shows "Reading [name]..." header', () => {
      render(<HeadsUpOpponentPanel opponent={makeOpponent()} />);

      const header = document.querySelector('.panel-header');
      expect(header).toBeTruthy();
      expect(header!.textContent).toContain('Reading');
      expect(header!.textContent).toContain('The Dark Knight');
    });

    it('shows play style label when enough hands observed', () => {
      render(<HeadsUpOpponentPanel opponent={makeOpponent()} />);

      const label = document.querySelector('.playstyle-label');
      expect(label).toBeTruthy();
      expect(label!.textContent).toBe('Loose & Aggressive');
    });

    it('shows VPIP and aggression stats', () => {
      render(<HeadsUpOpponentPanel opponent={makeOpponent()} />);

      const details = document.querySelector('.playstyle-details');
      expect(details).toBeTruthy();
      expect(details!.textContent).toContain('65% VPIP');
      expect(details!.textContent).toContain('Very aggressive');
    });

    it('shows hands observed count', () => {
      render(<HeadsUpOpponentPanel opponent={makeOpponent()} />);

      const handsObserved = document.querySelector('.hands-observed');
      expect(handsObserved).toBeTruthy();
      expect(handsObserved!.textContent).toContain('10 hands observed');
    });
  });

  describe('Case 2: With tilt', () => {
    it('shows tilt section with severity label for moderate tilt', () => {
      const opponent = makeOpponent({
        psychology: {
          tilt_level: 0.6,
          tilt_category: 'moderate',
          tilt_source: 'bad_beat',
          losing_streak: 0,
        },
      });

      render(<HeadsUpOpponentPanel opponent={opponent} />);

      const tiltSection = document.querySelector('.tilt-section');
      expect(tiltSection).toBeTruthy();
      expect(tiltSection!.classList.contains('moderate')).toBe(true);

      const tiltLabel = document.querySelector('.tilt-label');
      expect(tiltLabel).toBeTruthy();
      expect(tiltLabel!.textContent).toContain('Frustrated after bad beat');
    });

    it('shows tilt meter bar with correct width percentage', () => {
      const opponent = makeOpponent({
        psychology: {
          tilt_level: 0.6,
          tilt_category: 'moderate',
          tilt_source: 'bad_beat',
          losing_streak: 0,
        },
      });

      render(<HeadsUpOpponentPanel opponent={opponent} />);

      const meterFill = document.querySelector('.tilt-meter-fill') as HTMLElement;
      expect(meterFill).toBeTruthy();
      expect(meterFill.style.width).toBe('60%');
    });

    it('shows losing streak description for losing_streak tilt source', () => {
      const opponent = makeOpponent({
        psychology: {
          tilt_level: 0.4,
          tilt_category: 'mild',
          tilt_source: 'losing_streak',
          losing_streak: 4,
        },
      });

      render(<HeadsUpOpponentPanel opponent={opponent} />);

      const tiltLabel = document.querySelector('.tilt-label');
      expect(tiltLabel).toBeTruthy();
      expect(tiltLabel!.textContent).toContain('On a 4 hand losing streak');
    });
  });

  describe('Case 3: Calm state', () => {
    it('shows "Playing steady" text when no tilt and no emotional content', () => {
      const opponent = makeOpponent({
        psychology: {
          tilt_level: 0.0,
          tilt_category: 'none',
          losing_streak: 0,
        },
      });

      render(<HeadsUpOpponentPanel opponent={opponent} />);

      const calmSection = document.querySelector('.calm-section');
      expect(calmSection).toBeTruthy();
      const calmText = document.querySelector('.calm-text');
      expect(calmText).toBeTruthy();
      expect(calmText!.textContent).toBe('Playing steady');
    });

    it('does not show calm section when there is emotional content', () => {
      const opponent = makeOpponent({
        psychology: {
          tilt_level: 0.0,
          tilt_category: 'none',
          losing_streak: 0,
          narrative: 'Feeling confident after bluff',
          inner_voice: 'I got this',
        },
      });

      render(<HeadsUpOpponentPanel opponent={opponent} />);

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
    it('shows win/loss display', () => {
      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent({
            pressure_summary: makePressureSummary({ headsup_wins: 5, headsup_losses: 3 }),
          })}
        />
      );

      const statsSection = document.querySelector('.stats-section');
      expect(statsSection).toBeTruthy();

      const wins = document.querySelector('.wins');
      expect(wins).toBeTruthy();
      expect(wins!.textContent).toBe('5W');

      const losses = document.querySelector('.losses');
      expect(losses).toBeTruthy();
      expect(losses!.textContent).toBe('3L');
    });

    it('shows best pot won', () => {
      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent({
            pressure_summary: makePressureSummary({ biggest_pot_won: 500 }),
          })}
        />
      );

      const bestPot = document.querySelector('.biggest-pot');
      expect(bestPot).toBeTruthy();
      expect(bestPot!.textContent).toContain('$500');
    });

    it('shows signature move', () => {
      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent({
            pressure_summary: makePressureSummary({ signature_move: 'Late-position steals' }),
          })}
        />
      );

      const signatureMove = document.querySelector('.signature-move');
      expect(signatureMove).toBeTruthy();
      expect(signatureMove!.textContent).toContain('Late-position steals');
    });

    it('shows 0W - 0L when no stats available', () => {
      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent({ pressure_summary: undefined })}
        />
      );

      const wins = document.querySelector('.wins');
      expect(wins).toBeTruthy();
      expect(wins!.textContent).toBe('0W');

      const losses = document.querySelector('.losses');
      expect(losses).toBeTruthy();
      expect(losses!.textContent).toBe('0L');
    });
  });

  describe('Still reading state', () => {
    it('shows "Still reading..." when fewer than 10 hands observed', () => {
      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent({
            observation: makeObservation({ hands_observed: 2 }),
          })}
        />
      );

      const label = document.querySelector('.playstyle-label');
      expect(label).toBeTruthy();
      expect(label!.textContent).toBe('Still reading...');

      const handsObserved = document.querySelector('.hands-observed');
      expect(handsObserved).toBeTruthy();
      expect(handsObserved!.textContent).toContain('2 hands observed');
    });

    it('shows 0 hands observed when no observation data provided', () => {
      render(
        <HeadsUpOpponentPanel
          opponent={makeOpponent({ observation: undefined })}
        />
      );

      const label = document.querySelector('.playstyle-label');
      expect(label).toBeTruthy();
      expect(label!.textContent).toBe('Still reading...');

      const handsObserved = document.querySelector('.hands-observed');
      expect(handsObserved).toBeTruthy();
      expect(handsObserved!.textContent).toContain('0 hands observed');
    });
  });
});
