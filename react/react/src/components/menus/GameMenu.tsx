import { useState } from 'react';
import { Dices, Settings, Sparkles, FolderOpen, Theater, BarChart3, Microscope, ChevronRight } from 'lucide-react';
import { PageLayout, PageHeader } from '../shared';
import { config } from '../../config';
import './GameMenu.css';

interface GameMenuProps {
  playerName: string;
  onQuickPlay: () => void;
  onCustomGame: () => void;
  onThemedGame: () => void;
  onContinueGame: () => void;
  onManagePersonalities: () => void;
  onViewStats?: () => void;
  onPromptDebugger?: () => void;
  savedGamesCount: number;
}

export function GameMenu({
  playerName,
  onQuickPlay,
  onCustomGame,
  onThemedGame,
  onContinueGame,
  onManagePersonalities,
  onViewStats,
  onPromptDebugger,
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
            <Dices className="option-icon" size={24} />
            <div className="option-content">
              <h3>Quick Play</h3>
              <p>Jump into a random game with 3 AI opponents</p>
            </div>
            <ChevronRight className="option-arrow" size={20} />
          </button>

          <button 
            className="menu-option custom-game"
            onClick={onCustomGame}
            onMouseEnter={() => setHoveredOption('custom')}
            onMouseLeave={() => setHoveredOption(null)}
          >
            <Settings className="option-icon" size={24} />
            <div className="option-content">
              <h3>Custom Game</h3>
              <p>Choose your opponents and game settings</p>
            </div>
            <ChevronRight className="option-arrow" size={20} />
          </button>

          <button 
            className="menu-option themed-game"
            onClick={onThemedGame}
            onMouseEnter={() => setHoveredOption('themed')}
            onMouseLeave={() => setHoveredOption(null)}
          >
            <Sparkles className="option-icon" size={24} />
            <div className="option-content">
              <h3>Themed Game</h3>
              <p>Play with a surprise cast of personalities!</p>
            </div>
            <ChevronRight className="option-arrow" size={20} />
          </button>

          <button
            className="menu-option continue-game"
            onClick={onContinueGame}
            onMouseEnter={() => setHoveredOption('continue')}
            onMouseLeave={() => setHoveredOption(null)}
            disabled={savedGamesCount === 0}
          >
            <FolderOpen className="option-icon" size={24} />
            <div className="option-content">
              <h3>Continue Game</h3>
              <p>{savedGamesCount > 0
                ? `Resume from ${savedGamesCount} saved game${savedGamesCount > 1 ? 's' : ''}`
                : 'No saved games yet'
              }</p>
            </div>
            {savedGamesCount > 0 && <ChevronRight className="option-arrow" size={20} />}
          </button>

          {playerName.toLowerCase() === 'jeff' && (
            <button
              className="menu-option manage-personalities"
              onClick={onManagePersonalities}
              onMouseEnter={() => setHoveredOption('personalities')}
              onMouseLeave={() => setHoveredOption(null)}
            >
              <Theater className="option-icon" size={24} />
              <div className="option-content">
                <h3>Manage Personalities</h3>
                <p>Create and edit AI opponents</p>
              </div>
              <ChevronRight className="option-arrow" size={20} />
            </button>
          )}

          {onViewStats && (
            <button
              className="menu-option view-stats"
              onClick={onViewStats}
              onMouseEnter={() => setHoveredOption('stats')}
              onMouseLeave={() => setHoveredOption(null)}
            >
              <BarChart3 className="option-icon" size={24} />
              <div className="option-content">
                <h3>My Stats</h3>
                <p>View your career statistics and history</p>
              </div>
              <ChevronRight className="option-arrow" size={20} />
            </button>
          )}

          {config.ENABLE_DEBUG && onPromptDebugger && (
            <button
              className="menu-option prompt-debugger"
              onClick={onPromptDebugger}
              onMouseEnter={() => setHoveredOption('debugger')}
              onMouseLeave={() => setHoveredOption(null)}
            >
              <Microscope className="option-icon" size={24} />
              <div className="option-content">
                <h3>Prompt Debugger</h3>
                <p>Analyze and replay AI decision prompts</p>
              </div>
              <ChevronRight className="option-arrow" size={20} />
            </button>
          )}
        </div>

      <div className="game-menu__footer">
        <p className="tip">
          {hoveredOption === 'quick' && "Perfect for a quick session! Games typically last 20-30 minutes."}
          {hoveredOption === 'custom' && "Take full control - choose exactly who sits at your table."}
          {hoveredOption === 'themed' && "Each theme brings together personalities that create unique dynamics!"}
          {hoveredOption === 'continue' && savedGamesCount > 0 && "Pick up right where you left off."}
          {hoveredOption === 'personalities' && "Design unique AI opponents with custom traits and play styles."}
          {hoveredOption === 'stats' && "Track your wins, eliminations, and tournament history."}
          {hoveredOption === 'debugger' && "Debug AI decisions by viewing and replaying captured prompts."}
          {!hoveredOption && "Ready to test your poker face?"}
        </p>
      </div>
    </PageLayout>
  );
}