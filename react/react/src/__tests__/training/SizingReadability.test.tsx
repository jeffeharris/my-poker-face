import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { SizingReadability } from '../../components/training/SizingReadability';

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

const base = { face_up_threshold: 0.15, confirm_min_bets: 20 };
const read = (over = {}) => ({
  label: 'Your big bets are face-up',
  verdict: 'face_up',
  score: 0.33,
  big_eq: 0.7,
  small_eq: 0.37,
  confidence: 'high',
  stability: 'stable',
  n_bets: 41,
  n_big: 24,
  n_small: 17,
  advice: 'You almost only bet big with strength — mix some big bluffs in.',
  trend: { series: [0.31, 0.3, 0.33, 0.34, 0.33, 0.33] },
  ...over,
});

describe('SizingReadability (Surface A)', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it('shows your face-up leak + advice', async () => {
    mockFetch({ ...base, readability: read() });
    render(<SizingReadability />);
    await waitFor(() => expect(screen.getByText('Your big bets are face-up')).toBeTruthy());
    expect(screen.getByText('FACE-UP')).toBeTruthy();
    expect(screen.getByText(/mix some big bluffs/i)).toBeTruthy();
  });

  it('reads balanced as the clean state', async () => {
    mockFetch({
      ...base,
      readability: read({ verdict: 'balanced', label: 'Your sizing is balanced', advice: null }),
    });
    render(<SizingReadability />);
    await waitFor(() => expect(screen.getByText('Your sizing is balanced')).toBeTruthy());
    expect(screen.getByText(/can.t read your hand/i)).toBeTruthy();
  });

  it('shows keep-playing note when thin', async () => {
    mockFetch({
      ...base,
      readability: null,
      message: 'Not enough of your own big bets yet to read your sizing — keep playing.',
    });
    render(<SizingReadability />);
    await waitFor(() => expect(screen.getByText(/keep playing/)).toBeTruthy());
  });
});
