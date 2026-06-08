/**
 * Tests for useVisiblePolling — interval polling that pauses while the tab is
 * hidden and refreshes immediately on return.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useVisiblePolling } from './useVisiblePolling';

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { value: hidden, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
}

describe('useVisiblePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, 'hidden', { value: false, configurable: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(document, 'hidden', { value: false, configurable: true });
  });

  it('calls the callback immediately on mount when visible', () => {
    const cb = vi.fn();
    renderHook(() => useVisiblePolling(cb, 1000));
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it('polls on the interval while visible', () => {
    const cb = vi.fn();
    renderHook(() => useVisiblePolling(cb, 1000));
    expect(cb).toHaveBeenCalledTimes(1); // mount
    vi.advanceTimersByTime(3000);
    expect(cb).toHaveBeenCalledTimes(4); // + 3 ticks
  });

  it('pauses polling while the tab is hidden', () => {
    const cb = vi.fn();
    renderHook(() => useVisiblePolling(cb, 1000));
    cb.mockClear();

    setHidden(true);
    vi.advanceTimersByTime(5000);
    expect(cb).not.toHaveBeenCalled(); // no polling while hidden
  });

  it('refreshes immediately and resumes when the tab becomes visible again', () => {
    const cb = vi.fn();
    renderHook(() => useVisiblePolling(cb, 1000));
    setHidden(true);
    cb.mockClear();

    setHidden(false);
    expect(cb).toHaveBeenCalledTimes(1); // immediate refresh on return

    vi.advanceTimersByTime(2000);
    expect(cb).toHaveBeenCalledTimes(3); // + resumed ticks
  });

  it('does not start a duplicate interval on repeated visible events', () => {
    const cb = vi.fn();
    renderHook(() => useVisiblePolling(cb, 1000));
    cb.mockClear();

    // Two visible events in a row must not stack two intervals.
    setHidden(false);
    setHidden(false);
    cb.mockClear();

    vi.advanceTimersByTime(1000);
    expect(cb).toHaveBeenCalledTimes(1); // single interval, one tick
  });

  it('stops polling after unmount', () => {
    const cb = vi.fn();
    const { unmount } = renderHook(() => useVisiblePolling(cb, 1000));
    cb.mockClear();

    unmount();
    vi.advanceTimersByTime(5000);
    expect(cb).not.toHaveBeenCalled();
  });
});
