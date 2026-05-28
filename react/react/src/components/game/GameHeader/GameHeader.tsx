import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ChevronRight } from 'lucide-react';
import { formatCompactCurrency } from '../../../utils/formatters';
import './GameHeader.css';

interface GameHeaderProps {
  handNumber?: number;
  blinds: { small: number; big: number };
  phase: string;
  /** Cash-mode location: the friendly room name ("The Lodge") and stake
   *  tier ("$50"). When present, the header collapses to just the room
   *  name and the street badge is dropped (the board already tells you the
   *  street) — clicking the room name slides out the hand # and blinds.
   *  Omitted for tournament games, which keep the full inline info. */
  location?: { tableName?: string | null; stakeLabel?: string | null };
  onBackClick?: () => void;
  onSettingsClick?: () => void;
}

export function GameHeader({
  handNumber,
  blinds,
  phase,
  location,
  onBackClick,
  onSettingsClick,
}: GameHeaderProps) {
  // Format phase for display (e.g., "PRE_FLOP" -> "Pre-Flop")
  const formatPhase = (p: string): string => {
    return p
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join('-');
  };

  const [detailsOpen, setDetailsOpen] = useState(false);
  const tableName = location?.tableName ?? null;
  const blindsText = `Blinds ${formatCompactCurrency(blinds.small)}/${formatCompactCurrency(blinds.big, false)}`;

  return (
    <header className="game-header glass">
      {/* Left: Back button / Logo */}
      <div className="game-header__left">
        {onBackClick && (
          <button
            className="game-header__back-btn btn-icon"
            onClick={onBackClick}
            aria-label="Back to menu"
          >
            <span className="back-arrow">&#8592;</span>
            <span className="back-text">Menu</span>
          </button>
        )}
      </div>

      {/* Center: Game info */}
      <div className="game-header__center">
        {tableName ? (
          // Cash mode: the room name is the whole identity. Click it to slide
          // out the hand # and blinds. No street badge — the board shows it.
          <button
            type="button"
            className="game-header__room-toggle"
            onClick={() => setDetailsOpen((open) => !open)}
            aria-expanded={detailsOpen}
            aria-label={detailsOpen ? 'Hide table details' : 'Show hand number and blinds'}
          >
            <span className="game-header__location" title={tableName}>
              {tableName}
            </span>
            <ChevronRight
              size={14}
              className={`game-header__room-chevron${detailsOpen ? ' is-open' : ''}`}
              aria-hidden="true"
            />
            <AnimatePresence initial={false}>
              {detailsOpen && (
                <motion.span
                  className="game-header__room-details"
                  initial={{ width: 0, opacity: 0 }}
                  animate={{ width: 'auto', opacity: 1 }}
                  exit={{ width: 0, opacity: 0 }}
                  transition={{ duration: 0.22, ease: 'easeOut' }}
                >
                  <span className="game-header__separator">&#8226;</span>
                  {handNumber !== undefined && (
                    <>
                      <span className="game-header__info-item">Hand #{handNumber}</span>
                      <span className="game-header__separator">&#8226;</span>
                    </>
                  )}
                  <span className="game-header__info-item">{blindsText}</span>
                </motion.span>
              )}
            </AnimatePresence>
          </button>
        ) : (
          // Tournament mode: full inline info, including the street badge.
          <>
            {handNumber !== undefined && (
              <span className="game-header__info-item">Hand #{handNumber}</span>
            )}
            <span className="game-header__separator">&#8226;</span>
            <span className="game-header__info-item">{blindsText}</span>
            <span className="game-header__separator">&#8226;</span>
            <span className="game-header__phase-badge">{formatPhase(phase)}</span>
          </>
        )}
      </div>

      {/* Right: Settings */}
      <div className="game-header__right">
        {onSettingsClick && (
          <button
            className="game-header__settings-btn btn-icon"
            onClick={onSettingsClick}
            aria-label="Settings"
          >
            <span className="settings-icon">&#9881;</span>
          </button>
        )}
      </div>
    </header>
  );
}
