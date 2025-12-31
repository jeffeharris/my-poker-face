import { ReactNode } from 'react';
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
      <span className="mobile-chat-toggle__icon">💬</span>
      {badgeCount !== undefined && badgeCount > 0 && (
        <span className="mobile-chat-toggle__badge">{badgeCount}</span>
      )}
    </button>
  );
}
