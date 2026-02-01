import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useViewport } from '../../hooks/useViewport';

function setViewportSize(width: number, height: number) {
  Object.defineProperty(window, 'innerWidth', { value: width, writable: true, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: height, writable: true, configurable: true });
}

describe('VT-09: useViewport hook — returns correct breakpoints', () => {
  const originalInnerWidth = window.innerWidth;
  const originalInnerHeight = window.innerHeight;

  afterEach(() => {
    // Restore original values
    Object.defineProperty(window, 'innerWidth', { value: originalInnerWidth, writable: true, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: originalInnerHeight, writable: true, configurable: true });
  });

  describe('Mobile viewport (width < 768)', () => {
    it('returns isMobile: true at width 375', () => {
      setViewportSize(375, 812);

      const { result } = renderHook(() => useViewport());

      expect(result.current.isMobile).toBe(true);
      expect(result.current.isTablet).toBe(false);
      expect(result.current.isDesktop).toBe(false);
      expect(result.current.width).toBe(375);
      expect(result.current.height).toBe(812);
    });
  });

  describe('Tablet viewport (768 <= width < 1024)', () => {
    it('returns isTablet: true at width 800', () => {
      setViewportSize(800, 1024);

      const { result } = renderHook(() => useViewport());

      expect(result.current.isMobile).toBe(false);
      expect(result.current.isTablet).toBe(true);
      expect(result.current.isDesktop).toBe(false);
      expect(result.current.width).toBe(800);
    });
  });

  describe('Desktop viewport (width >= 1024)', () => {
    it('returns isDesktop: true at width 1200', () => {
      setViewportSize(1200, 800);

      const { result } = renderHook(() => useViewport());

      expect(result.current.isMobile).toBe(false);
      expect(result.current.isTablet).toBe(false);
      expect(result.current.isDesktop).toBe(true);
      expect(result.current.width).toBe(1200);
    });
  });

  describe('Portrait detection', () => {
    it('returns isPortrait: true when height > width', () => {
      setViewportSize(375, 812);

      const { result } = renderHook(() => useViewport());

      expect(result.current.isPortrait).toBe(true);
    });

    it('returns isPortrait: false when width > height', () => {
      setViewportSize(1200, 800);

      const { result } = renderHook(() => useViewport());

      expect(result.current.isPortrait).toBe(false);
    });
  });

  describe('Resize events', () => {
    it('updates values when window is resized', () => {
      setViewportSize(375, 812);

      const { result } = renderHook(() => useViewport());

      expect(result.current.isMobile).toBe(true);
      expect(result.current.isDesktop).toBe(false);

      // Simulate resize to desktop
      act(() => {
        setViewportSize(1200, 800);
        window.dispatchEvent(new Event('resize'));
      });

      expect(result.current.isMobile).toBe(false);
      expect(result.current.isDesktop).toBe(true);
      expect(result.current.width).toBe(1200);
      expect(result.current.height).toBe(800);
      expect(result.current.isPortrait).toBe(false);
    });

    it('updates on orientationchange event', () => {
      setViewportSize(375, 812);

      const { result } = renderHook(() => useViewport());

      expect(result.current.isPortrait).toBe(true);

      // Simulate orientation change to landscape
      act(() => {
        setViewportSize(812, 375);
        window.dispatchEvent(new Event('orientationchange'));
      });

      expect(result.current.isPortrait).toBe(false);
      expect(result.current.width).toBe(812);
      expect(result.current.height).toBe(375);
    });
  });

  describe('Boundary values', () => {
    it('width 768 is tablet, not mobile', () => {
      setViewportSize(768, 1024);

      const { result } = renderHook(() => useViewport());

      expect(result.current.isMobile).toBe(false);
      expect(result.current.isTablet).toBe(true);
    });

    it('width 1024 is desktop, not tablet', () => {
      setViewportSize(1024, 768);

      const { result } = renderHook(() => useViewport());

      expect(result.current.isTablet).toBe(false);
      expect(result.current.isDesktop).toBe(true);
    });

    it('width 767 is mobile', () => {
      setViewportSize(767, 1024);

      const { result } = renderHook(() => useViewport());

      expect(result.current.isMobile).toBe(true);
      expect(result.current.isTablet).toBe(false);
    });
  });
});
