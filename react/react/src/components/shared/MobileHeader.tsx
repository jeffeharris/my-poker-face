import { memo, useState, type ReactNode } from 'react';
import { ChevronRight, MessageCircle } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { BackButton } from './BackButton';
import { formatCompactCurrency } from '../../utils/formatters';
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
        {onBack && <BackButton onClick={onBack} variant="mobile" position="relative" />}
      </div>

      <div className="mobile-header__center">{centerContent}</div>

      <div className="mobile-header__right">{rightContent}</div>
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
export const PotDisplay = memo(function PotDisplay({ total }: PotDisplayProps) {
  return (
    <div className="mobile-pot">
      <span className="mobile-pot__label">POT:</span>
      <span className="mobile-pot__amount">${total}</span>
    </div>
  );
});

export interface GameInfoDisplayProps {
  phase: string;
  smallBlind: number;
  bigBlind: number;
  handNumber?: number;
  /** Cash-mode room name ("The Lodge"). Shown as the leading item so the
   *  player knows which table they're at. Omitted for tournaments. */
  tableName?: string | null;
}

/**
 * Game info display for mobile header.
 *
 * Tournament: shows hand #, street, and blinds inline.
 * Cash (tableName present): collapses to just the room name and drops the
 * street badge (the board already shows the street) — tapping the room name
 * slides out the hand # and blinds.
 */
export const GameInfoDisplay = memo(function GameInfoDisplay({
  phase,
  smallBlind,
  bigBlind,
  handNumber,
  tableName,
}: GameInfoDisplayProps) {
  // Format phase for display (e.g., "PRE_FLOP" -> "Pre-Flop")
  const formatPhase = (p: string) => {
    return p
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join('-');
  };

  // Fallback: small blind is typically half of big blind
  const displaySmallBlind = smallBlind || Math.floor(bigBlind / 2);
  const blindsText = `${formatCompactCurrency(displaySmallBlind)}/${formatCompactCurrency(bigBlind, false)}`;

  const [detailsOpen, setDetailsOpen] = useState(false);

  if (tableName) {
    return (
      <button
        type="button"
        className="mobile-game-info mobile-game-info--toggle"
        onClick={() => setDetailsOpen((open) => !open)}
        aria-expanded={detailsOpen}
        aria-label={detailsOpen ? 'Hide table details' : 'Show hand number and blinds'}
      >
        <span className="mobile-game-info__location" title={tableName}>
          {tableName}
        </span>
        <ChevronRight
          size={14}
          className={`mobile-game-info__chevron${detailsOpen ? ' is-open' : ''}`}
          aria-hidden="true"
        />
        <AnimatePresence initial={false}>
          {detailsOpen && (
            <motion.span
              className="mobile-game-info__details"
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: 'auto', opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.22, ease: 'easeOut' }}
            >
              {handNumber !== undefined && (
                <span className="mobile-game-info__hand">#{handNumber}</span>
              )}
              <span className="mobile-game-info__blinds">{blindsText}</span>
            </motion.span>
          )}
        </AnimatePresence>
      </button>
    );
  }

  return (
    <div className="mobile-game-info">
      {handNumber !== undefined && <span className="mobile-game-info__hand">#{handNumber}</span>}
      <span className="mobile-game-info__phase">{formatPhase(phase)}</span>
      <span className="mobile-game-info__blinds">{blindsText}</span>
    </div>
  );
});

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
