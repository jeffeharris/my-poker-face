import { useState } from 'react';
import {
  Zap,
  Medal,
  Coins,
  Settings,
  Sparkles,
  FolderOpen,
  BarChart3,
  ChevronRight,
  Trophy,
  Target,
  Flame,
  TrendingUp,
  Lock,
  Crown,
} from 'lucide-react';
import { PageLayout, PageHeader, MenuBar, UpgradeBanner } from '../shared';
import { useCareerStats } from '../../hooks/useCareerStats';
import { BLIND_PRESETS, type BlindPresetId } from '../../constants/gameStructure';

// Per-preset icon + accent class (visual only; the structure lives in
// gameStructure.ts so the tournament menu and Custom Game stay in sync).
const PRESET_VARIANT: Record<BlindPresetId, string> = {
  quick: 'quick-play-btn--lightning',
  tournament: 'quick-play-btn--random',
  deep: 'quick-play-btn--1v1',
};

function presetIcon(id: BlindPresetId) {
  if (id === 'quick') return <Zap className="quick-play-btn__icon" size={22} />;
  if (id === 'tournament') return <Medal className="quick-play-btn__icon" size={22} />;
  return <Coins className="quick-play-btn__icon" size={22} />;
}
import { useAuth } from '../../hooks/useAuth';
import { useViewport } from '../../hooks/useViewport';
import './TournamentMenu.css';

// ============================================
// Stats Sidebar Component
// ============================================

interface StatsSidebarProps {
  onViewFullStats?: () => void;
  isGuest?: boolean;
}

function StatsSidebar({ onViewFullStats, isGuest = false }: StatsSidebarProps) {
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
  const topNemesis =
    eliminatedPersonalities.length > 0
      ? eliminatedPersonalities.reduce((a, b) => (a.times_eliminated > b.times_eliminated ? a : b))
      : null;

  // Get last game result
  const lastGame = tournaments.length > 0 ? tournaments[0] : null;

  return (
    <aside className="game-menu__sidebar">
      {/* Upgrade Banner for Guests */}
      {isGuest && <UpgradeBanner variant="full" />}

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
              <span
                className={`sidebar__position sidebar__position--${lastGame.your_position === 1 ? 'win' : lastGame.your_position <= 2 ? 'top' : 'other'}`}
              >
                {lastGame.your_position === 1
                  ? '1st'
                  : lastGame.your_position === 2
                    ? '2nd'
                    : lastGame.your_position === 3
                      ? '3rd'
                      : `${lastGame.your_position}th`}
              </span>
              <span className="sidebar__game-meta">of {lastGame.player_count} players</span>
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
                <span className="sidebar__highlight-value">
                  ${stats.biggest_pot_ever.toLocaleString()}
                </span>
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
// Locked Menu Option (guest-only)
// ============================================

interface LockedMenuOptionProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  onClick: () => void;
  isGuest: boolean;
  isCreatingGame: boolean;
  hoverHandlers: { onMouseEnter?: () => void; onMouseLeave?: () => void };
  className: string;
}

function LockedMenuOption({
  icon,
  title,
  description,
  onClick,
  isGuest,
  isCreatingGame,
  hoverHandlers,
  className,
}: LockedMenuOptionProps) {
  return (
    <button
      className={`menu-option ${className} ${isGuest ? 'menu-option--locked' : ''}`}
      onClick={isGuest ? undefined : onClick}
      disabled={isCreatingGame || isGuest}
      {...hoverHandlers}
    >
      {isGuest ? <Lock className="option-icon option-icon--locked" size={24} /> : icon}
      <div className="option-content">
        <h3>
          {title}
          {isGuest && (
            <span className="pro-badge">
              <Crown size={12} /> Pro
            </span>
          )}
        </h3>
        <p>{isGuest ? 'Sign in with Google to unlock' : description}</p>
      </div>
      {!isGuest && <ChevronRight className="option-arrow" size={20} />}
    </button>
  );
}

// ============================================
// Main Component
// ============================================

export interface QuickPlayConfig {
  mode: 'quick' | 'tournament' | 'deep';
  opponents: number;
  startingBB: number;
  gameMode: string;
  blindGrowth: number;
  blindsIncrease: number;
  maxBlind: number;
}

interface TournamentMenuProps {
  playerName: string;
  onQuickPlay: (config: QuickPlayConfig) => void;
  onCustomGame: () => void;
  onThemedGame: () => void;
  onContinueGame: () => void;
  onViewStats?: () => void;
  onAdminDashboard?: () => void;
  onBack: () => void;
  savedGamesCount: number;
  isCreatingGame?: boolean;
}

export function TournamentMenu({
  playerName,
  onQuickPlay,
  onCustomGame,
  onThemedGame,
  onContinueGame,
  onViewStats,
  onAdminDashboard,
  onBack,
  savedGamesCount,
  isCreatingGame = false,
}: TournamentMenuProps) {
  const [hoveredOption, setHoveredOption] = useState<string | null>(null);
  const { isDesktop } = useViewport();
  const { user } = useAuth();
  const isGuest = user?.is_guest ?? true;
  const tablePlayers = isGuest ? 3 : 5;

  // Only use hover handlers on desktop
  const getHoverHandlers = (option: string) =>
    isDesktop
      ? {
          onMouseEnter: () => setHoveredOption(option),
          onMouseLeave: () => setHoveredOption(null),
        }
      : {};

  return (
    <>
      <MenuBar
        onBack={onBack}
        title="Tournaments"
        showUserInfo
        onMainMenu={onBack}
        onAdminTools={onAdminDashboard}
      />
      <PageLayout variant="top" glowColor="gold" maxWidth={isDesktop ? undefined : 'md'} hasMenuBar>
        <PageHeader
          title="Pick a Format"
          subtitle={`Ready, ${playerName}?`}
          titleVariant="primary"
        />

        <div className={`game-menu__layout ${isDesktop ? 'game-menu__layout--split' : ''}`}>
          {/* Main Menu Options */}
          <div className="game-menu__options">
            {/* Quick Start Variants */}
            <div className="quick-play-section">
              <h4 className="quick-play-section__title">Quick Start</h4>
              <div className="quick-play-section__buttons">
                {BLIND_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    className={`quick-play-btn ${PRESET_VARIANT[preset.id]}`}
                    onClick={() =>
                      onQuickPlay({
                        mode: preset.id,
                        opponents: tablePlayers,
                        startingBB: preset.startingBB,
                        gameMode: 'casual',
                        blindGrowth: preset.blindGrowth,
                        blindsIncrease: preset.blindsIncrease,
                        maxBlind: preset.maxBlind,
                      })
                    }
                    disabled={isCreatingGame}
                    {...getHoverHandlers(preset.id)}
                  >
                    {presetIcon(preset.id)}
                    <span className="quick-play-btn__label">{preset.label}</span>
                    <span className="quick-play-btn__meta">
                      {preset.startingBB} BB • ~{preset.estMinutes} min
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* Upgrade Banner for mobile guests - between quick play and custom */}
            {isGuest && !isDesktop && <UpgradeBanner variant="compact" />}

            <LockedMenuOption
              icon={<Settings className="option-icon" size={24} />}
              title="Custom Game"
              description="Choose your opponents and game settings"
              onClick={onCustomGame}
              isGuest={isGuest}
              isCreatingGame={isCreatingGame}
              hoverHandlers={getHoverHandlers('custom')}
              className="custom-game"
            />

            <LockedMenuOption
              icon={<Sparkles className="option-icon" size={24} />}
              title="Themed Game"
              description="Play with a surprise cast of personalities!"
              onClick={onThemedGame}
              isGuest={isGuest}
              isCreatingGame={isCreatingGame}
              hoverHandlers={getHoverHandlers('themed')}
              className="themed-game"
            />

            <button
              className="menu-option continue-game"
              onClick={onContinueGame}
              {...getHoverHandlers('continue')}
              disabled={savedGamesCount === 0}
            >
              <FolderOpen className="option-icon" size={24} />
              <div className="option-content">
                <h3>Continue Game</h3>
                <p>
                  {savedGamesCount > 0
                    ? `Resume from ${savedGamesCount} saved game${savedGamesCount > 1 ? 's' : ''}`
                    : 'No saved games yet'}
                </p>
              </div>
              {savedGamesCount > 0 && <ChevronRight className="option-arrow" size={20} />}
            </button>

            {/* Tournament Stats */}
            {onViewStats && (
              <button
                className="menu-option view-stats"
                onClick={onViewStats}
                {...getHoverHandlers('stats')}
              >
                <BarChart3 className="option-icon" size={24} />
                <div className="option-content">
                  <h3>Tournament Stats</h3>
                  <p>Your wins, eliminations, and history</p>
                </div>
                <ChevronRight className="option-arrow" size={20} />
              </button>
            )}
          </div>

          {/* Stats Sidebar - Desktop only */}
          {isDesktop && <StatsSidebar onViewFullStats={onViewStats} isGuest={isGuest} />}
        </div>

        {/* Footer tips - desktop only */}
        {isDesktop && (
          <div className="game-menu__footer">
            <p className="tip">
              {BLIND_PRESETS.find((p) => p.id === hoveredOption)?.blurb}
              {hoveredOption === 'custom' &&
                'Take full control - choose exactly who sits at your table.'}
              {hoveredOption === 'themed' &&
                'Each theme brings together personalities that create unique dynamics!'}
              {hoveredOption === 'continue' &&
                savedGamesCount > 0 &&
                'Pick up right where you left off.'}
              {hoveredOption === 'stats' &&
                'Track your wins, eliminations, and tournament history.'}
              {!hoveredOption && 'Ready to test your poker face?'}
            </p>
          </div>
        )}
      </PageLayout>
    </>
  );
}
