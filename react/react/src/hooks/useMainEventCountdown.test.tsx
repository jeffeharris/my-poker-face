/**
 * Tests for the Main Event countdown toast engine (useCountdownToasts).
 *
 * Covers the crossing semantics (fire on the downward transition past a
 * threshold), the no-late-fire guard (a threshold already passed on first sight
 * never fires), and per-invite dedup. Uses fake timers to drive the 1s ticker.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import toast from 'react-hot-toast';
import { useCountdownToasts, _resetAnnouncedForTest } from './useMainEventCountdown';

// The default export is called directly: toast(message, opts).
vi.mock('react-hot-toast', () => ({ default: vi.fn() }));

const FIVE_MIN = 'Main Event starts in 5 minutes';
const ONE_MIN = 'Main Event starts in 1 minute — register now';

describe('useCountdownToasts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _resetAnnouncedForTest(); // the dedup set is module-level — don't leak across cases
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-13T00:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('fires the 5-min then 1-min toast once each as the deadline is crossed', () => {
    const expiresAt = new Date(Date.now() + 6 * 60_000).toISOString();
    renderHook(() => useCountdownToasts(expiresAt, 'inv-cross'));

    // Cross the 5:00 mark (360s → 300s remaining).
    act(() => {
      vi.advanceTimersByTime(61_000);
    });
    expect(toast).toHaveBeenCalledTimes(1);
    expect(toast).toHaveBeenLastCalledWith(FIVE_MIN, expect.objectContaining({ icon: '🏆' }));

    // Cross the 1:00 mark (300s → 60s remaining).
    act(() => {
      vi.advanceTimersByTime(240_000);
    });
    expect(toast).toHaveBeenCalledTimes(2);
    expect(toast).toHaveBeenLastCalledWith(ONE_MIN, expect.objectContaining({ icon: '🏆' }));

    // Keep ticking to expiry — neither threshold re-fires (dedup).
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    expect(toast).toHaveBeenCalledTimes(2);
  });

  it('does not fire a threshold already passed on first sight', () => {
    // Mount with only 90s left: the 5-min mark is already behind us, so it must
    // never fire — only the genuine 1-min crossing does.
    const expiresAt = new Date(Date.now() + 90_000).toISOString();
    renderHook(() => useCountdownToasts(expiresAt, 'inv-late'));

    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(toast).toHaveBeenCalledTimes(1);
    expect(toast).toHaveBeenLastCalledWith(ONE_MIN, expect.objectContaining({ icon: '🏆' }));
  });

  it('idles when there is no open invite', () => {
    renderHook(() => useCountdownToasts(null, null));
    act(() => {
      vi.advanceTimersByTime(600_000);
    });
    expect(toast).not.toHaveBeenCalled();
  });

  it('does not re-fire a threshold for the same invite across remounts', () => {
    const expiresAt = new Date(Date.now() + 6 * 60_000).toISOString();
    const first = renderHook(() => useCountdownToasts(expiresAt, 'inv-remount'));
    act(() => {
      vi.advanceTimersByTime(61_000); // fires 5-min
    });
    expect(toast).toHaveBeenCalledTimes(1);
    first.unmount();

    // A fresh mount for the SAME invite (e.g. lobby → game handoff) must not
    // re-announce the 5-min threshold (module-level dedup).
    renderHook(() => useCountdownToasts(expiresAt, 'inv-remount'));
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    expect(toast).toHaveBeenCalledTimes(1);
  });
});
