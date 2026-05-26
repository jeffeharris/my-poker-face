import { formatCompactCurrency } from '../../../utils/formatters';
import './GameHeader.css';

interface GameHeaderProps {
  handNumber?: number;
  blinds: { small: number; big: number };
  phase: string;
  /** Cash-mode location: the friendly room name ("The Lodge") and stake
   *  tier ("$50"). When present, the room becomes the leading identity
   *  item. Omitted for tournament games. The room name is plain text for
   *  now — its click target is reserved for a future hand-replay view. */
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
        {location?.tableName && (
          <>
            <span className="game-header__location" title={location.tableName}>
              {location.tableName}
            </span>
            <span className="game-header__separator">&#8226;</span>
          </>
        )}
        {location?.stakeLabel && (
          <>
            <span className="game-header__info-item">{location.stakeLabel}</span>
            <span className="game-header__separator">&#8226;</span>
          </>
        )}
        {handNumber !== undefined && (
          <span className="game-header__info-item">Hand #{handNumber}</span>
        )}
        <span className="game-header__separator">&#8226;</span>
        <span className="game-header__info-item">
          Blinds {formatCompactCurrency(blinds.small)}/{formatCompactCurrency(blinds.big, false)}
        </span>
        <span className="game-header__separator">&#8226;</span>
        <span className="game-header__phase-badge">{formatPhase(phase)}</span>
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
