import type { CoachStats } from '../../types/coach';
import './StatsBar.css';

interface StatsBarProps {
  stats: CoachStats | null;
}

function equityColor(equity: number): string {
  if (equity >= 0.6) return 'var(--color-emerald, #4caf50)';
  if (equity >= 0.4) return 'var(--color-gold, #d4a574)';
  return 'var(--color-ruby, #e53935)';
}

function recClass(rec: string): string {
  switch (rec) {
    case 'fold': return 'rec-fold';
    case 'check': return 'rec-check';
    case 'call': return 'rec-call';
    case 'raise': return 'rec-raise';
    default: return '';
  }
}

export function StatsBar({ stats }: StatsBarProps) {
  if (!stats) return null;

  const equity = stats.equity;
  const eqPct = equity != null ? Math.round(equity * 100) : null;
  const reqPct = stats.required_equity != null ? Math.round(stats.required_equity * 100) : null;

  return (
    <div className="stats-bar">
      <div className="stats-bar-grid">
        {/* Equity */}
        <div className="stat-item">
          <span className="stat-label">Equity</span>
          {eqPct != null ? (
            <>
              <div className="stat-gauge">
                <div
                  className="stat-gauge-fill"
                  style={{
                    width: `${eqPct}%`,
                    background: equityColor(equity!),
                  }}
                />
              </div>
              <span className="stat-value" style={{ color: equityColor(equity!) }}>
                {eqPct}%
              </span>
              {stats.is_positive_ev != null && (
                <span className={`stat-ev ${stats.is_positive_ev ? 'ev-pos' : 'ev-neg'}`}>
                  {stats.is_positive_ev ? '+EV' : '-EV'}
                </span>
              )}
            </>
          ) : (
            <span className="stat-placeholder">&mdash;</span>
          )}
        </div>

        {/* Pot Odds */}
        <div className="stat-item">
          <span className="stat-label">Pot Odds</span>
          {stats.pot_odds != null ? (
            <>
              <span className="stat-value">{stats.pot_odds}:1</span>
              {reqPct != null && (
                <span className="stat-sub">Need {reqPct}%</span>
              )}
            </>
          ) : (
            <span className="stat-placeholder">&mdash;</span>
          )}
        </div>

        {/* Hand Strength */}
        <div className="stat-item">
          <span className="stat-label">Hand</span>
          {stats.hand_strength ? (
            <>
              <span className="stat-value stat-hand">{stats.hand_strength}</span>
              {stats.hand_rank != null && (
                <span className="stat-sub">Rank {stats.hand_rank}</span>
              )}
            </>
          ) : (
            <span className="stat-placeholder">&mdash;</span>
          )}
        </div>

        {/* Outs */}
        <div className="stat-item">
          <span className="stat-label">Outs</span>
          {stats.outs != null ? (
            <span className="stat-value">{stats.outs}</span>
          ) : (
            <span className="stat-placeholder">&mdash;</span>
          )}
        </div>
      </div>

      {/* Player Stats */}
      {stats.player_stats && stats.player_stats.hands_observed >= 5 && (
        <div className="stats-player-row">
          <span className="stats-player-label">Your Play</span>
          <div className="stats-player-values">
            <span className="stats-player-stat">
              <span className="stats-player-key">VPIP</span>
              <span className="stats-player-val">{Math.round(stats.player_stats.vpip * 100)}%</span>
            </span>
            <span className="stats-player-stat">
              <span className="stats-player-key">PFR</span>
              <span className="stats-player-val">{Math.round(stats.player_stats.pfr * 100)}%</span>
            </span>
            <span className="stats-player-stat">
              <span className="stats-player-key">AGG</span>
              <span className="stats-player-val">{stats.player_stats.aggression.toFixed(1)}</span>
            </span>
            <span className="stats-player-stat">
              <span className="stats-player-key">Style</span>
              <span className="stats-player-val stats-player-style">{stats.player_stats.style}</span>
            </span>
          </div>
        </div>
      )}

      {/* Recommendation */}
      {stats.recommendation && (
        <div className={`stats-recommendation ${recClass(stats.recommendation)}`}>
          Recommendation: <strong>{stats.recommendation.toUpperCase()}</strong>
        </div>
      )}
    </div>
  );
}
