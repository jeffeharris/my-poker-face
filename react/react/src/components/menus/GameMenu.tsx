import { useState } from 'react';
import { PageLayout, PageHeader } from '../shared';
import './GameMenu.css';

interface GameMenuProps {
  playerName: string;
  onQuickPlay: () => void;
  onCustomGame: () => void;
  onThemedGame: () => void;
  onContinueGame: () => void;
  onManagePersonalities: () => void;
  savedGamesCount: number;
}

export function GameMenu({
  playerName,
  onQuickPlay,
  onCustomGame,
  onThemedGame,
  onContinueGame,
  onManagePersonalities,
  savedGamesCount
}: GameMenuProps) {
  const [hoveredOption, setHoveredOption] = useState<string | null>(null);

  return (
    <PageLayout variant="centered" glowColor="gold" maxWidth="md">
      <PageHeader
        title={`Welcome, ${playerName}!`}
        subtitle="Choose how you'd like to play"
        titleVariant="primary"
      />

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
            <div className="option-icon">✨</div>
            <div className="option-content">
              <h3>Themed Game</h3>
              <p>Play with a surprise cast of personalities!</p>
            </div>
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

          <button
            className="menu-option manage-personalities"
            onClick={onManagePersonalities}
            onMouseEnter={() => setHoveredOption('personalities')}
            onMouseLeave={() => setHoveredOption(null)}
          >
            <div className="option-icon">🎭</div>
            <div className="option-content">
              <h3>Manage Personalities</h3>
              <p>Create and edit AI opponents</p>
            </div>
            <div className="option-arrow">→</div>
          </button>
        </div>

      <div className="game-menu__footer">
        <p className="tip">
          {hoveredOption === 'quick' && "Perfect for a quick session! Games typically last 20-30 minutes."}
          {hoveredOption === 'custom' && "Take full control - choose exactly who sits at your table."}
          {hoveredOption === 'themed' && "Each theme brings together personalities that create unique dynamics!"}
          {hoveredOption === 'continue' && savedGamesCount > 0 && "Pick up right where you left off."}
          {hoveredOption === 'personalities' && "Design unique AI opponents with custom traits and play styles."}
          {!hoveredOption && "Ready to test your poker face?"}
        </p>
      </div>
    </PageLayout>
  );
}