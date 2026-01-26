import { useState } from 'react';
import { PageLayout } from '../../shared/PageLayout';
import { PageHeader } from '../../shared/PageHeader';
import { MenuBar } from '../../shared/MenuBar';
import { useCareerStats } from '../../../hooks/useCareerStats';
import { getOrdinal } from '../../../types/tournament';
import type { EliminatedPersonality } from '../../../types/tournament';
import './CareerStats.css';

interface CareerStatsProps {
  onBack: () => void;
}

export function CareerStats({ onBack }: CareerStatsProps) {
  const { stats, tournaments, eliminatedPersonalities, loading, error, refresh } = useCareerStats();
  const [selectedBadge, setSelectedBadge] = useState<EliminatedPersonality | null>(null);

  if (loading) {
    return (
      <>
        <MenuBar onBack={onBack} title="My Stats" showUserInfo onMainMenu={onBack} />
        <PageLayout variant="centered" glowColor="sapphire" hasMenuBar>
          <div className="career-stats-loading">
            <div className="loading-spinner" />
            <p>Loading your stats...</p>
          </div>
        </PageLayout>
      </>
    );
  }

  if (error) {
    return (
      <>
        <MenuBar onBack={onBack} title="My Stats" showUserInfo onMainMenu={onBack} />
        <PageLayout variant="centered" glowColor="sapphire" hasMenuBar>
          <div className="career-stats-error">
            <span className="error-icon">!</span>
            <p>{error}</p>
            <button className="retry-button" onClick={refresh}>
              Try Again
            </button>
          </div>
        </PageLayout>
      </>
    );
  }

  // Empty state - no games played yet
  if (!stats || stats.games_played === 0) {
    return (
      <>
        <MenuBar onBack={onBack} title="My Stats" showUserInfo onMainMenu={onBack} />
        <PageLayout variant="centered" glowColor="sapphire" hasMenuBar>
          <div className="career-stats-empty">
            <div className="empty-icon">&#x1F3B0;</div>
            <h2>No Stats Yet</h2>
            <p>Play your first tournament to start tracking your poker career!</p>
            <button className="start-playing-button" onClick={onBack}>
              Start Playing
            </button>
          </div>
        </PageLayout>
      </>
    );
  }

  const winRate = Math.round((stats.win_rate || 0) * 100);

  return (
    <>
      <MenuBar onBack={onBack} title="My Stats" showUserInfo onMainMenu={onBack} />
      <PageLayout variant="top" glowColor="sapphire" hasMenuBar>
        <PageHeader title="My Stats" />

      <div className="career-stats">
        {/* Hero Stats */}
        <div className="stats-hero">
          <div className="stat-card">
            <span className="stat-value">{stats.games_played}</span>
            <span className="stat-label">Games</span>
          </div>
          <div className="stat-card highlight">
            <span className="stat-value">{winRate}%</span>
            <span className="stat-label">Win Rate</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{stats.games_won}</span>
            <span className="stat-label">Wins</span>
          </div>
        </div>

        {/* Achievements Row */}
        <div className="achievements-row">
          <div className="achievement">
            <span className="achievement-icon">&#x1F480;</span>
            <span className="achievement-label">Takeouts</span>
            <span className="achievement-value">{stats.total_eliminations}</span>
          </div>
          {stats.best_finish && (
            <div className="achievement">
              <span className="achievement-icon">&#x1F3C6;</span>
              <span className="achievement-label">Best Finish</span>
              <span className="achievement-value">{getOrdinal(stats.best_finish)}</span>
            </div>
          )}
          {stats.biggest_pot_ever > 0 && (
            <div className="achievement">
              <span className="achievement-icon">&#x1F4B0;</span>
              <span className="achievement-label">Biggest Pot</span>
              <span className="achievement-value">${stats.biggest_pot_ever.toLocaleString()}</span>
            </div>
          )}
        </div>

        {/* Tournament History */}
        {tournaments.length > 0 && (
          <div className="history-section">
            <h3 className="section-title">Recent Tournaments</h3>
            <div className="tournament-list">
              {tournaments.map((t) => (
                <div key={t.game_id} className={`tournament-row ${t.your_position === 1 ? 'winner' : ''}`}>
                  <span className="position-badge">
                    {t.your_position === 1 ? '\u{1F947}' :
                     t.your_position === 2 ? '\u{1F948}' :
                     t.your_position === 3 ? '\u{1F949}' :
                     '\u{1F480}'} {getOrdinal(t.your_position)}
                  </span>
                  <div className="tournament-details">
                    <span className="tournament-hands">{t.total_hands} hands</span>
                    {t.eliminated_by && (
                      <span className="eliminated-by">by {t.eliminated_by}</span>
                    )}
                  </div>
                  <span className="tournament-date">
                    {new Date(t.ended_at).toLocaleDateString()}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Personality Badges Collection */}
        <div className="badges-section">
          <h3 className="section-title">
            Collection
            <span className="badge-count">
              {eliminatedPersonalities.length} eliminated
            </span>
          </h3>

          {eliminatedPersonalities.length > 0 ? (
            <>
              <div className="badges-grid">
                {eliminatedPersonalities.map((personality) => (
                  <button
                    key={personality.name}
                    className={`personality-badge ${selectedBadge?.name === personality.name ? 'selected' : ''}`}
                    onClick={() => setSelectedBadge(
                      selectedBadge?.name === personality.name ? null : personality
                    )}
                    title={personality.name}
                  >
                    <span className="badge-initial">
                      {personality.name.charAt(0).toUpperCase()}
                    </span>
                    {personality.times_eliminated > 1 && (
                      <span className="badge-count-indicator">
                        x{personality.times_eliminated}
                      </span>
                    )}
                  </button>
                ))}
              </div>

              {/* Selected Badge Info */}
              {selectedBadge && (
                <div className="badge-info">
                  <div className="badge-info-header">
                    <span className="badge-info-name">{selectedBadge.name}</span>
                    <button
                      className="badge-info-close"
                      onClick={() => setSelectedBadge(null)}
                    >
                      x
                    </button>
                  </div>
                  <div className="badge-info-details">
                    <span>Eliminated {selectedBadge.times_eliminated}x</span>
                    <span>First: {new Date(selectedBadge.first_eliminated_at).toLocaleDateString()}</span>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="badges-empty">
              <p>Eliminate AI players to collect their badges!</p>
            </div>
          )}
        </div>
      </div>
      </PageLayout>
    </>
  );
}
