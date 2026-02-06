import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import { InterhandTransition } from '../../components/mobile/InterhandTransition';

describe('InterhandTransition', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('visibility', () => {
    it('renders when isVisible is true', () => {
      render(<InterhandTransition isVisible={true} />);
      expect(screen.getByTestId('interhand-transition')).toBeTruthy();
    });

    it('returns null when isVisible is false', () => {
      const { container } = render(<InterhandTransition isVisible={false} />);
      expect(container.firstChild).toBeNull();
    });

    it('removes content when isVisible changes from true to false', () => {
      const { rerender } = render(<InterhandTransition isVisible={true} />);
      expect(screen.getByTestId('interhand-transition')).toBeTruthy();

      rerender(<InterhandTransition isVisible={false} />);
      expect(screen.queryByTestId('interhand-transition')).toBeNull();
    });
  });

  describe('two-layer structure', () => {
    it('renders dim layer with correct test id', () => {
      render(<InterhandTransition isVisible={true} />);
      const dimLayer = screen.getByTestId('interhand-transition');
      expect(dimLayer.classList.contains('interhand-dim')).toBe(true);
    });

    it('renders content layer', () => {
      render(<InterhandTransition isVisible={true} />);
      const contentLayer = document.querySelector('.interhand-content-layer');
      expect(contentLayer).toBeTruthy();
    });

    it('renders ambient particles in dim layer', () => {
      render(<InterhandTransition isVisible={true} />);
      const particles = document.querySelectorAll('.ambient-particle');
      expect(particles.length).toBe(12);
    });

    it('renders shuffle cards in content layer', () => {
      render(<InterhandTransition isVisible={true} />);
      const shuffleCards = document.querySelectorAll('.shuffle-card');
      expect(shuffleCards.length).toBe(8);
    });
  });

  describe('hand number display', () => {
    it('displays hand number as #{handNumber + 1}', async () => {
      render(<InterhandTransition isVisible={true} handNumber={5} />);

      // Wait for content visibility delay (100ms)
      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      // Hand number 5 should display as #6 (next hand)
      expect(screen.getByText('#6')).toBeTruthy();
      expect(screen.getByText('Next Hand')).toBeTruthy();
    });

    it('does not display hand badge when handNumber is not provided', async () => {
      render(<InterhandTransition isVisible={true} />);

      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      expect(screen.queryByText('Next Hand')).toBeNull();
    });

    it('does not display hand badge when handNumber is 0 (falsy)', async () => {
      // Note: The component uses {handNumber && ...} which treats 0 as falsy
      render(<InterhandTransition isVisible={true} handNumber={0} />);

      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      // handNumber=0 is falsy so badge should not appear
      expect(screen.queryByText('Next Hand')).toBeNull();
    });
  });

  describe('content visibility delay', () => {
    it('content is not visible initially', () => {
      render(<InterhandTransition isVisible={true} />);
      const content = document.querySelector('.interhand-content');
      expect(content?.classList.contains('visible')).toBe(false);
    });

    it('content becomes visible after 100ms delay', async () => {
      render(<InterhandTransition isVisible={true} />);

      // Content should not be visible yet
      const content = document.querySelector('.interhand-content');
      expect(content?.classList.contains('visible')).toBe(false);

      // Advance by 100ms
      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      // Content should now be visible (check synchronously after timer advance)
      const updatedContent = document.querySelector('.interhand-content');
      expect(updatedContent?.classList.contains('visible')).toBe(true);
    });

    it('content visibility resets when isVisible becomes false', async () => {
      const { rerender } = render(<InterhandTransition isVisible={true} />);

      // Wait for content to become visible
      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      // Make invisible
      rerender(<InterhandTransition isVisible={false} />);

      // Make visible again
      rerender(<InterhandTransition isVisible={true} />);

      // Content should start hidden again
      const content = document.querySelector('.interhand-content');
      expect(content?.classList.contains('visible')).toBe(false);
    });
  });

  describe('status text', () => {
    it('displays "Shuffling" status text', async () => {
      render(<InterhandTransition isVisible={true} />);

      await act(async () => {
        vi.advanceTimersByTime(100);
      });

      expect(screen.getByText('Shuffling')).toBeTruthy();
    });

    it('displays animated dots', () => {
      render(<InterhandTransition isVisible={true} />);
      const dots = document.querySelectorAll('.status-dots .dot');
      expect(dots.length).toBe(3);
    });
  });
});
