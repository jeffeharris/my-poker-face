import { useState } from 'react';
import './GameMenu.css';

interface GameMenuProps {
  playerName: string;
  onQuickPlay: () => void;
  onCustomGame: () => void;
  onThemedGame: () => void;
  onContinueGame: () => void;
  savedGamesCount: number;
}

export function GameMenu({ 
  playerName, 
  onQuickPlay, 
  onCustomGame, 
  onThemedGame, 
  onContinueGame,
  savedGamesCount 
}: GameMenuProps) {
  const [hoveredOption, setHoveredOption] = useState<string | null>(null);

  return (
    <div className="game-menu">
      <div className="game-menu__container">
        <div className="game-menu__header">
          <h1>Welcome, {playerName}!</h1>
          <p>Choose how you'd like to play</p>
        </div>

        <div className="game-menu__options">
          <button 
            className="menu-option quick-play"
            onClick={onQuickPlay}
            onMouseEnter={() => setHoveredOption('quick')}
            onMouseLeave={() => setHoveredOption(null)}
          >
            <div className="option-icon">🎲</div>
            <div className="option-content">
              <h3>Quick Play</h3>
              <p>Jump into a random game with 3 AI opponents</p>
            </div>
            <div className="option-arrow">→</div>
          </button>

          <button 
            className="menu-option custom-game"
            onClick={onCustomGame}
            onMouseEnter={() => setHoveredOption('custom')}
            onMouseLeave={() => setHoveredOption(null)}
          >
            <div className="option-icon">⚙️</div>
            <div className="option-content">
              <h3>Custom Game</h3>
              <p>Choose your opponents and game settings</p>
            </div>
            <div className="option-arrow">→</div>
          </button>

          <button 
            className="menu-option themed-game"
            onClick={onThemedGame}
            onMouseEnter={() => setHoveredOption('themed')}
            onMouseLeave={() => setHoveredOption(null)}
          >
            <div className="option-icon">🎭</div>
            <div className="option-content">
              <h3>Themed Game</h3>
              <p>Play with a surprise cast of personalities!</p>
            </div>
            <div className="option-badge">NEW</div>
            <div className="option-arrow">→</div>
          </button>

          <button 
            className="menu-option continue-game"
            onClick={onContinueGame}
            onMouseEnter={() => setHoveredOption('continue')}
            onMouseLeave={() => setHoveredOption(null)}
            disabled={savedGamesCount === 0}
          >
            <div className="option-icon">📂</div>
            <div className="option-content">
              <h3>Continue Game</h3>
              <p>{savedGamesCount > 0 
                ? `Resume from ${savedGamesCount} saved game${savedGamesCount > 1 ? 's' : ''}`
                : 'No saved games yet'
              }</p>
            </div>
            {savedGamesCount > 0 && <div className="option-arrow">→</div>}
          </button>
        </div>

        <div className="game-menu__footer">
          <p className="tip">
            {hoveredOption === 'quick' && "Perfect for a quick session! Games typically last 20-30 minutes."}
            {hoveredOption === 'custom' && "Take full control - choose exactly who sits at your table."}
            {hoveredOption === 'themed' && "Each theme brings together personalities that create unique dynamics!"}
            {hoveredOption === 'continue' && savedGamesCount > 0 && "Pick up right where you left off."}
            {!hoveredOption && "🃏 Ready to test your poker face?"}
          </p>
        </div>
      </div>
    </div>
  );
}