import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { OpponentSizingTell } from '../../components/character/OpponentSizingTell';

// Sparkline needs an SVG/layout env we don't care about here — stub it.
vi.mock('../../components/cash/Sparkline', () => ({
  Sparkline: () => <div data-testid="spark" />,
}));
vi.mock('../../config', () => ({ config: { API_URL: '' } }));
vi.mock('../../utils/logger', () => ({ logger: { error: vi.fn() } }));

function mockFetch(payload: unknown, ok = true) {
  global.fetch = vi.fn().mockResolvedValue({
    ok,
    status: ok ? 200 : 500,
    json: () => Promise.resolve(payload),
  }) as unknown as typeof fetch;
}

const FACE_UP = {
  opponent: 'Batman',
  face_up_threshold: 0.15,
  confirm_min_bets: 20,
  tells: [
    {
      axis: 'sizing',
      label: 'Big bets = strength',
      verdict: 'face_up',
      score: 0.41,
      big_eq: 0.76,
      small_eq: 0.35,
      confidence: 'high',
      stability: 'stable',
      n_bets: 63,
      n_big: 40,
      n_small: 23,
      exploit: 'Fold your marginal hands to their big bets — they size up with strength.',
      trend: { series: [0.38, 0.4, 0.39, 0.42, 0.41, 0.41] },
    },
  ],
};

describe('OpponentSizingTell', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it('renders the verdict, exploit, and pill for a face-up read', async () => {
    mockFetch(FACE_UP);
    render(<OpponentSizingTell opponent="Batman" />);
    await waitFor(() => expect(screen.getByText('Big bets = strength')).toBeTruthy());
    expect(screen.getByText('FACE-UP')).toBeTruthy();
    expect(screen.getByText(/Fold your marginal hands/)).toBeTruthy();
    expect(screen.getByText(/score \+0\.41/)).toBeTruthy();
  });

  it('shows the mixing warning when the tell is going stale', async () => {
    mockFetch({ ...FACE_UP, tells: [{ ...FACE_UP.tells[0], stability: 'mixing' }] });
    render(<OpponentSizingTell opponent="Batman" />);
    await waitFor(() => expect(screen.getByText(/starting to mix/)).toBeTruthy());
  });

  it('shows the keep-playing note when the sample is too thin', async () => {
    mockFetch({
      opponent: 'Newbie',
      face_up_threshold: 0.15,
      confirm_min_bets: 20,
      tells: [],
      message: "Not enough of Newbie's big bets seen yet to read their sizing — keep playing them.",
    });
    render(<OpponentSizingTell opponent="Newbie" />);
    await waitFor(() => expect(screen.getByText(/keep playing them/)).toBeTruthy());
  });

  it('renders nothing on a fetch error', async () => {
    mockFetch({}, false);
    const { container } = render(<OpponentSizingTell opponent="Batman" />);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(container.querySelector('.sizing-tell')).toBeNull();
  });
});
