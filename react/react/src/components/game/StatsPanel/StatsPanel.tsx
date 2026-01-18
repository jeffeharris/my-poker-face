import type { Player } from '../../../types/player';
import './StatsPanel.css';

interface StatsPanelProps {
  humanPlayer: Player;
  players: Player[];
  potTotal: number;
  handNumber?: number;
}

export function StatsPanel({
  humanPlayer,
  players,
  potTotal,
  handNumber,
}: StatsPanelProps) {
  // Calculate table stats
  const activePlayers = players.filter(p => !p.is_folded);
  const averageStack = Math.round(
    players.reduce((sum, p) => sum + p.stack, 0) / players.length
  );
  const opponents = players.filter(p => !p.is_human);

  return (
    <div className="stats-panel">
      {/* Your Stats Card */}
      <div className="stats-card premium-card">
        <h3 className="stats-card__title">Your Stats</h3>

        <div className="stats-card__main">
          <div className="stats-card__stack">
            <span className="stats-card__stack-value gradient-text">
              ${humanPlayer.stack.toLocaleString()}
            </span>
            <span className="stats-card__stack-label">Current Stack</span>
          </div>
        </div>

        <div className="stats-card__details">
          {humanPlayer.is_folded && (
            <div className="stats-card__status folded">FOLDED</div>
          )}
          {humanPlayer.is_all_in && (
            <div className="stats-card__status all-in">ALL-IN</div>
          )}
        </div>
      </div>

      {/* Table Overview Card */}
      <div className="stats-card premium-card">
        <h3 className="stats-card__title">Table Overview</h3>

        <div className="stats-card__row">
          <span className="stats-card__label">Current Pot</span>
          <span className="stats-card__value highlight">${potTotal.toLocaleString()}</span>
        </div>

        <div className="stats-card__row">
          <span className="stats-card__label">Average Stack</span>
          <span className="stats-card__value">${averageStack.toLocaleString()}</span>
        </div>

        <div className="stats-card__row">
          <span className="stats-card__label">Active Players</span>
          <span className="stats-card__value">
            {activePlayers.length} / {players.length}
          </span>
        </div>

        {handNumber !== undefined && (
          <div className="stats-card__row">
            <span className="stats-card__label">Hand</span>
            <span className="stats-card__value">#{handNumber}</span>
          </div>
        )}
      </div>

      {/* Opponents Quick View */}
      <div className="stats-card premium-card">
        <h3 className="stats-card__title">Opponents</h3>

        <div className="opponent-list">
          {opponents.map(opponent => {
            const stackPercentage = Math.min(100, (opponent.stack / averageStack) * 100);

            return (
              <div
                key={opponent.name}
                className={`opponent-item ${opponent.is_folded ? 'folded' : ''} ${opponent.is_all_in ? 'all-in' : ''}`}
              >
                <div className="opponent-item__header">
                  <span className="opponent-item__name">{opponent.name}</span>
                  <span className="opponent-item__stack">${opponent.stack.toLocaleString()}</span>
                </div>
                <div className="opponent-item__bar">
                  <div
                    className="opponent-item__bar-fill"
                    style={{ width: `${stackPercentage}%` }}
                  />
                </div>
                {opponent.is_folded && <span className="opponent-item__status">Folded</span>}
                {opponent.is_all_in && <span className="opponent-item__status">All-in</span>}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
