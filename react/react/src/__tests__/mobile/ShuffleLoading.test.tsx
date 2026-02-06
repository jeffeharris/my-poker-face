import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { act } from 'react';
import { ShuffleLoading } from '../../components/shared/ShuffleLoading';

describe('ShuffleLoading', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('visibility', () => {
    it('renders when isVisible is true', () => {
      render(<ShuffleLoading isVisible={true} message="Loading" />);
      expect(screen.getByTestId('shuffle-loading')).toBeTruthy();
    });

    it('returns null when isVisible is false', () => {
      const { container } = render(<ShuffleLoading isVisible={false} message="Loading" />);
      expect(container.firstChild).toBeNull();
    });

    it('removes content when isVisible changes from true to false', () => {
      const { rerender } = render(<ShuffleLoading isVisible={true} message="Loading" />);
      expect(screen.getByTestId('shuffle-loading')).toBeTruthy();

      rerender(<ShuffleLoading isVisible={false} message="Loading" />);
      expect(screen.queryByTestId('shuffle-loading')).toBeNull();
    });
  });

  describe('overlay variant (default)', () => {
    it('renders a single overlay layer', () => {
      render(<ShuffleLoading isVisible={true} message="Loading" />);
      const overlay = document.querySelector('.shuffle-loading-overlay');
      expect(overlay).toBeTruthy();
    });

    it('does not render interhand layers', () => {
      render(<ShuffleLoading isVisible={true} message="Loading" />);
      expect(document.querySelector('.shuffle-loading-dim')).toBeNull();
      expect(document.querySelector('.shuffle-loading-content-layer')).toBeNull();
    });

    it('renders shuffle cards', () => {
      render(<ShuffleLoading isVisible={true} message="Loading" />);
      const shuffleCards = document.querySelectorAll('.shuffle-loading-card');
      expect(shuffleCards.length).toBe(8);
    });
  });

  describe('interhand variant', () => {
    it('renders dim layer with correct test id', () => {
      render(<ShuffleLoading isVisible={true} message="Shuffling" variant="interhand" />);
      const dimLayer = screen.getByTestId('shuffle-loading');
      expect(dimLayer.classList.contains('shuffle-loading-dim')).toBe(true);
    });

    it('renders content layer', () => {
      render(<ShuffleLoading isVisible={true} message="Shuffling" variant="interhand" />);
      const contentLayer = document.querySelector('.shuffle-loading-content-layer');
      expect(contentLayer).toBeTruthy();
    });

    it('does not render overlay layer', () => {
      render(<ShuffleLoading isVisible={true} message="Shuffling" variant="interhand" />);
      expect(document.querySelector('.shuffle-loading-overlay')).toBeNull();
    });

    it('renders shuffle cards in content layer', () => {
      render(<ShuffleLoading isVisible={true} message="Shuffling" variant="interhand" />);
      const shuffleCards = document.querySelectorAll('.shuffle-loading-card');
      expect(shuffleCards.length).toBe(8);
    });
  });

  describe('message and submessage', () => {
    it('displays the provided message', async () => {
      render(<ShuffleLoading isVisible={true} message="Setting up your game" />);

      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      expect(screen.getByText('Setting up your game')).toBeTruthy();
    });

    it('displays submessage when provided', async () => {
      render(<ShuffleLoading isVisible={true} message="Loading" submessage="Please wait" />);

      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      expect(screen.getByText('Please wait')).toBeTruthy();
    });

    it('does not display submessage when not provided', () => {
      render(<ShuffleLoading isVisible={true} message="Loading" />);
      expect(document.querySelector('.shuffle-loading-submessage')).toBeNull();
    });
  });

  describe('hand number display', () => {
    it('displays hand number as #{handNumber + 1}', async () => {
      render(<ShuffleLoading isVisible={true} message="Shuffling" handNumber={5} />);

      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      expect(screen.getByText('#6')).toBeTruthy();
      expect(screen.getByText('Next Hand')).toBeTruthy();
    });

    it('does not display hand badge when handNumber is not provided', async () => {
      render(<ShuffleLoading isVisible={true} message="Shuffling" />);

      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      expect(screen.queryByText('Next Hand')).toBeNull();
    });

    it('does not display hand badge when handNumber is 0', async () => {
      render(<ShuffleLoading isVisible={true} message="Shuffling" handNumber={0} />);

      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      expect(screen.queryByText('Next Hand')).toBeNull();
    });
  });

  describe('content visibility delay', () => {
    it('content is not visible initially', () => {
      render(<ShuffleLoading isVisible={true} message="Loading" />);
      const content = document.querySelector('.shuffle-loading-content');
      expect(content?.classList.contains('visible')).toBe(false);
    });

    it('content becomes visible after 100ms delay', async () => {
      render(<ShuffleLoading isVisible={true} message="Loading" />);

      const content = document.querySelector('.shuffle-loading-content');
      expect(content?.classList.contains('visible')).toBe(false);

      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      const updatedContent = document.querySelector('.shuffle-loading-content');
      expect(updatedContent?.classList.contains('visible')).toBe(true);
    });

    it('content visibility resets when isVisible becomes false', async () => {
      const { rerender } = render(<ShuffleLoading isVisible={true} message="Loading" />);

      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      rerender(<ShuffleLoading isVisible={false} message="Loading" />);
      rerender(<ShuffleLoading isVisible={true} message="Loading" />);

      const content = document.querySelector('.shuffle-loading-content');
      expect(content?.classList.contains('visible')).toBe(false);
    });
  });

  describe('animated dots', () => {
    it('displays animated dots', () => {
      render(<ShuffleLoading isVisible={true} message="Loading" />);
      const dots = document.querySelectorAll('.shuffle-loading-dots .dot');
      expect(dots.length).toBe(3);
    });
  });
});
