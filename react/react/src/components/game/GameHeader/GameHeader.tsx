import './GameHeader.css';

interface GameHeaderProps {
  handNumber?: number;
  blinds: { small: number; big: number };
  phase: string;
  onBackClick?: () => void;
  onSettingsClick?: () => void;
}

export function GameHeader({
  handNumber,
  blinds,
  phase,
  onBackClick,
  onSettingsClick,
}: GameHeaderProps) {
  // Format phase for display (e.g., "PRE_FLOP" -> "Pre-Flop")
  const formatPhase = (p: string): string => {
    return p
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
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
        {handNumber !== undefined && (
          <span className="game-header__info-item">
            Hand #{handNumber}
          </span>
        )}
        <span className="game-header__separator">&#8226;</span>
        <span className="game-header__info-item">
          Blinds ${blinds.small}/${blinds.big}
        </span>
        <span className="game-header__separator">&#8226;</span>
        <span className="game-header__phase-badge">
          {formatPhase(phase)}
        </span>
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
