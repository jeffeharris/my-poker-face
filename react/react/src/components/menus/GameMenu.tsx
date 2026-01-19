import { useState } from 'react';
import { Zap, Users, Shuffle, Settings, Sparkles, FolderOpen, BarChart3, Microscope, FlaskConical, ChevronRight, LayoutDashboard, Trophy, Target, Flame, TrendingUp } from 'lucide-react';
import { PageLayout, PageHeader } from '../shared';
import { config } from '../../config';
import { useCareerStats } from '../../hooks/useCareerStats';
import { useViewport } from '../../hooks/useViewport';
import './GameMenu.css';

// ============================================
// Stats Sidebar Component
// ============================================

interface StatsSidebarProps {
  onViewFullStats?: () => void;
}

function StatsSidebar({ onViewFullStats }: StatsSidebarProps) {
  const { stats, tournaments, eliminatedPersonalities, loading } = useCareerStats();

  if (loading) {
    return (
      <aside className="game-menu__sidebar">
        <div className="sidebar__section">
          <div className="sidebar__loading">
            <div className="sidebar__loading-spinner" />
            <span>Loading stats...</span>
          </div>
        </div>
      </aside>
    );
  }

  // Get top nemesis (most eliminated)
  const topNemesis = eliminatedPersonalities.length > 0
    ? eliminatedPersonalities.reduce((a, b) => a.times_eliminated > b.times_eliminated ? a : b)
    : null;

  // Get last game result
  const lastGame = tournaments.length > 0 ? tournaments[0] : null;

  return (
    <aside className="game-menu__sidebar">
      {/* Quick Stats */}
      <div className="sidebar__section">
        <h3 className="sidebar__title">
          <BarChart3 size={18} />
          Quick Stats
        </h3>
        {stats ? (
          <div className="sidebar__stats-grid">
            <div className="sidebar__stat">
              <span className="sidebar__stat-value">{stats.games_played}</span>
              <span className="sidebar__stat-label">Games</span>
            </div>
            <div className="sidebar__stat sidebar__stat--highlight">
              <span className="sidebar__stat-value">{Math.round(stats.win_rate * 100)}%</span>
              <span className="sidebar__stat-label">Win Rate</span>
            </div>
            <div className="sidebar__stat">
              <span className="sidebar__stat-value">{stats.games_won}</span>
              <span className="sidebar__stat-label">Wins</span>
            </div>
            <div className="sidebar__stat">
              <span className="sidebar__stat-value">{stats.total_eliminations}</span>
              <span className="sidebar__stat-label">KOs</span>
            </div>
          </div>
        ) : (
          <p className="sidebar__empty">Play your first game to see stats!</p>
        )}
      </div>

      {/* Recent Game */}
      {lastGame && (
        <div className="sidebar__section">
          <h3 className="sidebar__title">
            <Trophy size={18} />
            Last Game
          </h3>
          <div className="sidebar__recent-game">
            <div className="sidebar__game-result">
              <span className={`sidebar__position sidebar__position--${lastGame.your_position === 1 ? 'win' : lastGame.your_position <= 2 ? 'top' : 'other'}`}>
                {lastGame.your_position === 1 ? '1st' : lastGame.your_position === 2 ? '2nd' : lastGame.your_position === 3 ? '3rd' : `${lastGame.your_position}th`}
              </span>
              <span className="sidebar__game-meta">
                of {lastGame.player_count} players
              </span>
            </div>
            {lastGame.your_position === 1 ? (
              <p className="sidebar__game-detail sidebar__game-detail--win">Victory!</p>
            ) : lastGame.eliminated_by ? (
              <p className="sidebar__game-detail">Eliminated by {lastGame.eliminated_by}</p>
            ) : null}
          </div>
        </div>
      )}

      {/* Top Nemesis */}
      {topNemesis && topNemesis.times_eliminated >= 2 && (
        <div className="sidebar__section">
          <h3 className="sidebar__title">
            <Target size={18} />
            Nemesis
          </h3>
          <div className="sidebar__nemesis">
            <span className="sidebar__nemesis-name">{topNemesis.name}</span>
            <span className="sidebar__nemesis-count">
              <Flame size={14} />
              {topNemesis.times_eliminated} eliminations
            </span>
          </div>
        </div>
      )}

      {/* Streak / Achievements placeholder */}
      {stats && stats.games_won > 0 && (
        <div className="sidebar__section">
          <h3 className="sidebar__title">
            <TrendingUp size={18} />
            Highlights
          </h3>
          <div className="sidebar__highlights">
            {stats.biggest_pot_ever > 0 && (
              <div className="sidebar__highlight">
                <span className="sidebar__highlight-label">Biggest Pot</span>
                <span className="sidebar__highlight-value">${stats.biggest_pot_ever.toLocaleString()}</span>
              </div>
            )}
            {stats.best_finish === 1 && (
              <div className="sidebar__highlight sidebar__highlight--gold">
                <span className="sidebar__highlight-label">Best Finish</span>
                <span className="sidebar__highlight-value">1st Place</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* View Full Stats Link */}
      {onViewFullStats && (
        <button className="sidebar__view-all" onClick={onViewFullStats}>
          View Full Stats
          <ChevronRight size={16} />
        </button>
      )}
    </aside>
  );
}

// ============================================
// Main Component
// ============================================

export interface QuickPlayConfig {
  mode: 'lightning' | '1v1' | 'random';
  opponents: number;
  startingBB: number;
}

interface GameMenuProps {
  playerName: string;
  onQuickPlay: (config: QuickPlayConfig) => void;
  onCustomGame: () => void;
  onThemedGame: () => void;
  onContinueGame: () => void;
  onViewStats?: () => void;
  onPromptDebugger?: () => void;
  onPromptPlayground?: () => void;
  onAdminDashboard?: () => void;
  savedGamesCount: number;
}

export function GameMenu({
  playerName,
  onQuickPlay,
  onCustomGame,
  onThemedGame,
  onContinueGame,
  onViewStats,
  onPromptDebugger,
  onPromptPlayground,
  onAdminDashboard,
  savedGamesCount
}: GameMenuProps) {
  const [hoveredOption, setHoveredOption] = useState<string | null>(null);
  const { isDesktop } = useViewport();

  return (
    <PageLayout variant="centered" glowColor="gold" maxWidth={isDesktop ? 'xl' : 'md'}>
      <PageHeader
        title={`Welcome, ${playerName}!`}
        subtitle="Choose how you'd like to play"
        titleVariant="primary"
      />

      <div className={`game-menu__layout ${isDesktop ? 'game-menu__layout--split' : ''}`}>
        {/* Main Menu Options */}
        <div className="game-menu__options">
          {/* Quick Play Variants */}
          <div className="quick-play-section">
            <h4 className="quick-play-section__title">Quick Play</h4>
            <div className="quick-play-section__buttons">
              <button
                className="quick-play-btn quick-play-btn--lightning"
                onClick={() => onQuickPlay({ mode: 'lightning', opponents: 5, startingBB: 10 })}
                onMouseEnter={() => setHoveredOption('lightning')}
                onMouseLeave={() => setHoveredOption(null)}
              >
                <Zap className="quick-play-btn__icon" size={22} />
                <span className="quick-play-btn__label">Lightning</span>
                <span className="quick-play-btn__meta">10BB • 5 players</span>
              </button>

              <button
                className="quick-play-btn quick-play-btn--1v1"
                onClick={() => onQuickPlay({ mode: '1v1', opponents: 1, startingBB: 20 })}
                onMouseEnter={() => setHoveredOption('1v1')}
                onMouseLeave={() => setHoveredOption(null)}
              >
                <Users className="quick-play-btn__icon" size={22} />
                <span className="quick-play-btn__label">1v1</span>
                <span className="quick-play-btn__meta">Heads up</span>
              </button>

              <button
                className="quick-play-btn quick-play-btn--random"
                onClick={() => onQuickPlay({ mode: 'random', opponents: 4, startingBB: 20 })}
                onMouseEnter={() => setHoveredOption('random')}
                onMouseLeave={() => setHoveredOption(null)}
              >
                <Shuffle className="quick-play-btn__icon" size={22} />
                <span className="quick-play-btn__label">Classic</span>
                <span className="quick-play-btn__meta">20BB • 4 players</span>
              </button>
            </div>
          </div>

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

          {/* My Stats - only show on mobile, desktop has sidebar */}
          {!isDesktop && onViewStats && (
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

          {playerName.toLowerCase() === 'jeff' && onAdminDashboard && (
            <button
              className="menu-option admin-dashboard"
              onClick={onAdminDashboard}
              onMouseEnter={() => setHoveredOption('admin')}
              onMouseLeave={() => setHoveredOption(null)}
            >
              <LayoutDashboard className="option-icon" size={24} />
              <div className="option-content">
                <h3>Admin Tools</h3>
                <p>Personalities, experiments, and prompt tools</p>
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

          {config.ENABLE_DEBUG && onPromptPlayground && (
            <button
              className="menu-option prompt-playground"
              onClick={onPromptPlayground}
              onMouseEnter={() => setHoveredOption('playground')}
              onMouseLeave={() => setHoveredOption(null)}
            >
              <FlaskConical className="option-icon" size={24} />
              <div className="option-content">
                <h3>Prompt Playground</h3>
                <p>View and replay any captured LLM prompt</p>
              </div>
              <ChevronRight className="option-arrow" size={20} />
            </button>
          )}
        </div>

        {/* Stats Sidebar - Desktop only */}
        {isDesktop && (
          <StatsSidebar onViewFullStats={onViewStats} />
        )}
      </div>

      <div className="game-menu__footer">
        <p className="tip">
          {hoveredOption === 'lightning' && "Fast and furious! Short stacks mean quick decisions and big swings."}
          {hoveredOption === '1v1' && "Test your skills head-to-head against a single AI opponent."}
          {hoveredOption === 'random' && "The classic experience with a comfortable stack and 4 opponents."}
          {hoveredOption === 'custom' && "Take full control - choose exactly who sits at your table."}
          {hoveredOption === 'themed' && "Each theme brings together personalities that create unique dynamics!"}
          {hoveredOption === 'continue' && savedGamesCount > 0 && "Pick up right where you left off."}
          {hoveredOption === 'admin' && "All admin tools in one place: personalities, experiments, and prompts."}
          {hoveredOption === 'stats' && "Track your wins, eliminations, and tournament history."}
          {hoveredOption === 'debugger' && "Debug AI decisions by viewing and replaying captured prompts."}
          {hoveredOption === 'playground' && "Explore and replay any LLM prompt with different models."}
          {!hoveredOption && "Ready to test your poker face?"}
        </p>
      </div>
    </PageLayout>
  );
}
