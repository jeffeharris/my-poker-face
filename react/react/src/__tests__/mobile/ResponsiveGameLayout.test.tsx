import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ResponsiveGameLayout } from '../../components/shared/ResponsiveGameLayout';

// Mock useViewport hook
const mockUseViewport = vi.fn();
vi.mock('../../hooks/useViewport', () => ({
  useViewport: () => mockUseViewport(),
}));

// Track props passed to mocked components
let lastMobileProps: Record<string, unknown> = {};
let lastDesktopProps: Record<string, unknown> = {};

// Mock MobilePokerTable
vi.mock('../../components/mobile', () => ({
  MobilePokerTable: (props: Record<string, unknown>) => {
    lastMobileProps = props;
    return (
      <div data-testid="mobile-poker-table">
        MobilePokerTable
      </div>
    );
  },
}));

// Mock PokerTable
vi.mock('../../components/game/PokerTable', () => ({
  PokerTable: (props: Record<string, unknown>) => {
    lastDesktopProps = props;
    return (
      <div data-testid="poker-table">
        PokerTable
      </div>
    );
  },
}));

function mobileViewport() {
  mockUseViewport.mockReturnValue({
    isMobile: true,
    isTablet: false,
    isDesktop: false,
    width: 375,
    height: 812,
    isPortrait: true,
  });
}

function desktopViewport() {
  mockUseViewport.mockReturnValue({
    isMobile: false,
    isTablet: false,
    isDesktop: true,
    width: 1200,
    height: 800,
    isPortrait: false,
  });
}

describe('VT-10: ResponsiveGameLayout — routes to MobilePokerTable on mobile', () => {
  const defaultProps = {
    gameId: 'test-game-123',
    playerName: 'TestPlayer',
    onGameCreated: vi.fn(),
    onBack: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    lastMobileProps = {};
    lastDesktopProps = {};
  });

  describe('Mobile viewport (< 768px)', () => {
    it('renders MobilePokerTable when isMobile is true', () => {
      mobileViewport();

      render(<ResponsiveGameLayout {...defaultProps} />);

      expect(screen.getByTestId('mobile-poker-table')).toBeInTheDocument();
      expect(screen.queryByTestId('poker-table')).not.toBeInTheDocument();
    });

    it('passes gameId, playerName, onGameCreated, and onBack to MobilePokerTable', () => {
      mobileViewport();

      render(<ResponsiveGameLayout {...defaultProps} />);

      expect(lastMobileProps.gameId).toBe('test-game-123');
      expect(lastMobileProps.playerName).toBe('TestPlayer');
      expect(typeof lastMobileProps.onGameCreated).toBe('function');
      expect(typeof lastMobileProps.onBack).toBe('function');
    });
  });

  describe('Desktop viewport (>= 1024px)', () => {
    it('renders PokerTable when isMobile is false', () => {
      desktopViewport();

      render(<ResponsiveGameLayout {...defaultProps} />);

      expect(screen.getByTestId('poker-table')).toBeInTheDocument();
      expect(screen.queryByTestId('mobile-poker-table')).not.toBeInTheDocument();
    });

    it('passes gameId, playerName, and onGameCreated to PokerTable (no onBack)', () => {
      desktopViewport();

      render(<ResponsiveGameLayout {...defaultProps} />);

      expect(lastDesktopProps.gameId).toBe('test-game-123');
      expect(lastDesktopProps.playerName).toBe('TestPlayer');
      expect(typeof lastDesktopProps.onGameCreated).toBe('function');
      // onBack is NOT passed to PokerTable
      expect(lastDesktopProps).not.toHaveProperty('onBack');
    });
  });

  describe('Props passthrough', () => {
    it('handles null gameId on mobile', () => {
      mobileViewport();

      render(<ResponsiveGameLayout {...defaultProps} gameId={null} />);

      expect(screen.getByTestId('mobile-poker-table')).toBeInTheDocument();
      expect(lastMobileProps.gameId).toBeNull();
    });

    it('handles undefined optional props on desktop', () => {
      desktopViewport();

      render(<ResponsiveGameLayout />);

      expect(screen.getByTestId('poker-table')).toBeInTheDocument();
    });
  });
});
