import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { GuestLimitModal } from '../../components/shared/GuestLimitModal';

// Mock lucide-react
vi.mock('lucide-react', () => ({
  Crown: ({ size, ...props }: { size?: number } & Record<string, unknown>) => (
    <span data-testid="crown-icon" {...props}>Crown</span>
  ),
  Shield: ({ size, ...props }: { size?: number } & Record<string, unknown>) => (
    <span data-testid="shield-icon" {...props}>Shield</span>
  ),
  Users: ({ size, ...props }: { size?: number } & Record<string, unknown>) => (
    <span data-testid="users-icon" {...props}>Users</span>
  ),
  Sparkles: ({ size, ...props }: { size?: number } & Record<string, unknown>) => (
    <span data-testid="sparkles-icon" {...props}>Sparkles</span>
  ),
  Settings: ({ size, ...props }: { size?: number } & Record<string, unknown>) => (
    <span data-testid="settings-icon" {...props}>Settings</span>
  ),
}));

// Mock config
vi.mock('../../config', () => ({
  config: {
    API_URL: 'http://localhost:5000',
  },
}));

function makeProps(overrides: Partial<Parameters<typeof GuestLimitModal>[0]> = {}) {
  return {
    handsPlayed: 20,
    handsLimit: 20,
    onReturnToMenu: vi.fn(),
    ...overrides,
  };
}

describe('VT-08: GuestLimitModal — content, CTA, benefits grid', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('Title and subtitle', () => {
    it('shows hand count in title', () => {
      const props = makeProps();
      render(<GuestLimitModal {...props} />);
      expect(screen.getByText("You've played 20 hands!")).toBeTruthy();
    });

    it('shows different hand count when changed', () => {
      const props = makeProps({ handsPlayed: 15 });
      render(<GuestLimitModal {...props} />);
      expect(screen.getByText("You've played 15 hands!")).toBeTruthy();
    });

    it('subtitle mentions hand limit', () => {
      const props = makeProps();
      render(<GuestLimitModal {...props} />);
      const subtitle = screen.getByText(/Guest accounts are limited to 20 hands/);
      expect(subtitle).toBeTruthy();
    });

    it('subtitle mentions signing in with Google', () => {
      const props = makeProps();
      render(<GuestLimitModal {...props} />);
      const subtitle = screen.getByText(/Sign in with Google to keep playing/);
      expect(subtitle).toBeTruthy();
    });
  });

  describe('Benefits grid', () => {
    it('renders 4 benefits', () => {
      const props = makeProps();
      const { container } = render(<GuestLimitModal {...props} />);
      const benefits = container.querySelectorAll('.guest-limit-modal__benefit');
      expect(benefits.length).toBe(4);
    });

    it('shows "Unlimited hands" benefit', () => {
      const props = makeProps();
      render(<GuestLimitModal {...props} />);
      expect(screen.getByText('Unlimited hands')).toBeTruthy();
    });

    it('shows "Up to 9 AI opponents" benefit', () => {
      const props = makeProps();
      render(<GuestLimitModal {...props} />);
      expect(screen.getByText('Up to 9 AI opponents')).toBeTruthy();
    });

    it('shows "Custom game wizard" benefit', () => {
      const props = makeProps();
      render(<GuestLimitModal {...props} />);
      expect(screen.getByText('Custom game wizard')).toBeTruthy();
    });

    it('shows "Themed game experiences" benefit', () => {
      const props = makeProps();
      render(<GuestLimitModal {...props} />);
      expect(screen.getByText('Themed game experiences')).toBeTruthy();
    });
  });

  describe('CTA button', () => {
    it('renders "Sign in with Google" CTA button', () => {
      const props = makeProps();
      const { container } = render(<GuestLimitModal {...props} />);
      const cta = container.querySelector('.guest-limit-modal__cta');
      expect(cta).toBeTruthy();
      expect(cta!.textContent).toContain('Sign in with Google');
    });

    it('CTA button navigates to Google auth on click', () => {
      const props = makeProps();
      const { container } = render(<GuestLimitModal {...props} />);

      // Mock window.location.href
      const originalLocation = window.location;
      const mockLocation = { ...originalLocation, href: '' };
      Object.defineProperty(window, 'location', {
        writable: true,
        value: mockLocation,
      });

      const cta = container.querySelector('.guest-limit-modal__cta') as HTMLButtonElement;
      fireEvent.click(cta);

      expect(mockLocation.href).toBe('http://localhost:5000/api/auth/google/login');

      // Restore
      Object.defineProperty(window, 'location', {
        writable: true,
        value: originalLocation,
      });
    });
  });

  describe('Return to menu button', () => {
    it('renders "Return to Main Menu" button', () => {
      const props = makeProps();
      const { container } = render(<GuestLimitModal {...props} />);
      const secondary = container.querySelector('.guest-limit-modal__secondary');
      expect(secondary).toBeTruthy();
      expect(secondary!.textContent).toContain('Return to Main Menu');
    });

    it('calls onReturnToMenu when clicked', () => {
      const props = makeProps();
      const { container } = render(<GuestLimitModal {...props} />);
      const secondary = container.querySelector('.guest-limit-modal__secondary') as HTMLButtonElement;
      fireEvent.click(secondary);
      expect(props.onReturnToMenu).toHaveBeenCalledTimes(1);
    });

    it('does not render secondary button when onReturnToMenu is not provided', () => {
      const props = makeProps({ onReturnToMenu: undefined });
      const { container } = render(<GuestLimitModal {...props} />);
      const secondary = container.querySelector('.guest-limit-modal__secondary');
      expect(secondary).toBeNull();
    });
  });

  describe('Structure', () => {
    it('renders overlay', () => {
      const props = makeProps();
      const { container } = render(<GuestLimitModal {...props} />);
      expect(container.querySelector('.guest-limit-modal__overlay')).toBeTruthy();
    });

    it('renders shield icon', () => {
      const props = makeProps();
      render(<GuestLimitModal {...props} />);
      expect(screen.getByTestId('shield-icon')).toBeTruthy();
    });

    it('renders preservation note', () => {
      const props = makeProps();
      render(<GuestLimitModal {...props} />);
      expect(screen.getByText(/progress and stats will be preserved/)).toBeTruthy();
    });
  });
});
