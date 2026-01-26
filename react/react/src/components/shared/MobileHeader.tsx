import type { ReactNode } from 'react';
import { MessageCircle } from 'lucide-react';
import { BackButton } from './BackButton';
import './MobileHeader.css';

export interface MobileHeaderProps {
  /** Back button click handler (if provided, shows back button) */
  onBack?: () => void;
  /** Center content (e.g., pot display, title) */
  centerContent?: ReactNode;
  /** Right content (e.g., chat toggle, menu button) */
  rightContent?: ReactNode;
  /** Additional class name */
  className?: string;
}

/**
 * Unified mobile header for game screens.
 *
 * Provides consistent layout with:
 * - Left: Optional back button
 * - Center: Flexible content slot
 * - Right: Flexible content slot
 *
 * Includes safe area inset support for notched devices.
 */
export function MobileHeader({
  onBack,
  centerContent,
  rightContent,
  className = '',
}: MobileHeaderProps) {
  return (
    <header className={`mobile-header ${className}`.trim()}>
      <div className="mobile-header__left">
        {onBack && (
          <BackButton onClick={onBack} variant="mobile" position="relative" />
        )}
      </div>

      <div className="mobile-header__center">
        {centerContent}
      </div>

      <div className="mobile-header__right">
        {rightContent}
      </div>
    </header>
  );
}

/* ===========================================
   COMMON HEADER WIDGETS
   =========================================== */

export interface PotDisplayProps {
  total: number;
}

/**
 * Pot amount display for mobile header center slot.
 */
export function PotDisplay({ total }: PotDisplayProps) {
  return (
    <div className="mobile-pot">
      <span className="mobile-pot__label">POT</span>
      <span className="mobile-pot__amount">${total}</span>
    </div>
  );
}

export interface GameInfoDisplayProps {
  phase: string;
  smallBlind: number;
  bigBlind: number;
}

/**
 * Game info display for mobile header - shows phase and blinds.
 */
export function GameInfoDisplay({ phase, smallBlind, bigBlind }: GameInfoDisplayProps) {
  // Format phase for display (e.g., "PRE_FLOP" -> "Pre-Flop")
  const formatPhase = (p: string) => {
    return p
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join('-');
  };

  return (
    <div className="mobile-game-info">
      <span className="mobile-game-info__phase">{formatPhase(phase)}</span>
      <span className="mobile-game-info__blinds">${smallBlind}/${bigBlind}</span>
    </div>
  );
}

export interface ChatToggleProps {
  onClick: () => void;
  badgeCount?: number;
}

/**
 * Chat toggle button for mobile header right slot.
 */
export function ChatToggle({ onClick, badgeCount }: ChatToggleProps) {
  return (
    <button className="mobile-chat-toggle" onClick={onClick}>
      <MessageCircle className="mobile-chat-toggle__icon" size={24} />
      {badgeCount !== undefined && badgeCount > 0 && (
        <span className="mobile-chat-toggle__badge">{badgeCount}</span>
      )}
    </button>
  );
}
