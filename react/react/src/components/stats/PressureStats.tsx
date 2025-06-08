import { useState, useEffect } from 'react';
import { Socket } from 'socket.io-client';
import { config } from '../../config';
import './PressureStats.css';

interface PlayerSummary {
  total_events: number;
  big_wins: number;
  big_losses: number;
  successful_bluffs: number;
  bluffs_caught: number;
  bad_beats: number;
  eliminations: number;
  biggest_pot_won: number;
  biggest_pot_lost: number;
  tilt_score: number;
  aggression_score: number;
  signature_move: string;
}

interface LeaderboardEntry {
  name: string;
  [key: string]: any;
}

interface SessionSummary {
  session_duration: number;
  total_events: number;
  biggest_pot: number;
  player_summaries: { [name: string]: PlayerSummary };
  leaderboards: {
    biggest_winners: LeaderboardEntry[];
    master_bluffers: LeaderboardEntry[];
    most_aggressive: LeaderboardEntry[];
    bad_beat_victims: LeaderboardEntry[];
    tilt_masters: LeaderboardEntry[];
  };
  fun_facts: string[];
}

interface PressureStatsProps {
  gameId: string | null;
  isOpen: boolean;
  socket?: Socket | null;
}

export function PressureStats({ gameId, isOpen, socket }: PressureStatsProps) {
  const [stats, setStats] = useState<SessionSummary | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!gameId || !isOpen) return;

    const fetchStats = async () => {
      try {
        setLoading(true);
        const response = await fetch(`${config.API_URL}/api/game/${gameId}/pressure-stats`);
        if (response.ok) {
          const data = await response.json();
          setStats(data);
        }
      } catch (error) {
        console.error('Failed to fetch pressure stats:', error);
      } finally {
        setLoading(false);
      }
    };

    // Fetch immediately
    fetchStats();

    // Update every 5 seconds
    const interval = setInterval(fetchStats, 5000);

    return () => clearInterval(interval);
  }, [gameId, isOpen]);

  if (!isOpen || !stats) return null;

  const getTiltEmoji = (score: number) => {
    if (score > 0.8) return '🤯';
    if (score > 0.6) return '😤';
    if (score > 0.4) return '😠';
    if (score > 0.2) return '😑';
    return '😊';
  };

  const getAggressionEmoji = (score: number) => {
    if (score > 0.8) return '🔥';
    if (score > 0.6) return '⚡';
    if (score > 0.4) return '💪';
    if (score > 0.2) return '👊';
    return '🤝';
  };

  return (
    <div className="pressure-stats-panel">
      <h3>🎰 Pressure Stats & Highlights</h3>
      
      {loading && <div className="loading">Loading stats...</div>}
      
      {/* Session Overview */}
      <div className="session-overview">
        <div className="stat-item">
          <span className="stat-label">Session Time:</span>
          <span className="stat-value">{stats.session_duration} minutes</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Dramatic Moments:</span>
          <span className="stat-value">{stats.total_events}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Biggest Pot:</span>
          <span className="stat-value highlight">${stats.biggest_pot}</span>
        </div>
      </div>

      {/* Leaderboards */}
      <div className="leaderboards">
        {stats.leaderboards.biggest_winners.length > 0 && (
          <div className="leaderboard">
            <h4>👑 Biggest Winners</h4>
            {stats.leaderboards.biggest_winners.map((entry, i) => (
              <div key={i} className="leaderboard-entry">
                <span className="rank">#{i + 1}</span>
                <span className="name">{entry.name}</span>
                <span className="value">{entry.wins} wins (${entry.biggest_pot})</span>
              </div>
            ))}
          </div>
        )}

        {stats.leaderboards.master_bluffers.length > 0 && (
          <div className="leaderboard">
            <h4>🎭 Master Bluffers</h4>
            {stats.leaderboards.master_bluffers.map((entry, i) => (
              <div key={i} className="leaderboard-entry">
                <span className="rank">#{i + 1}</span>
                <span className="name">{entry.name}</span>
                <span className="value">{entry.bluffs} bluffs</span>
              </div>
            ))}
          </div>
        )}

        {stats.leaderboards.tilt_masters.length > 0 && (
          <div className="leaderboard">
            <h4>😤 Tilt Masters</h4>
            {stats.leaderboards.tilt_masters.map((entry, i) => (
              <div key={i} className="leaderboard-entry">
                <span className="rank">#{i + 1}</span>
                <span className="name">{entry.name}</span>
                <span className="value">{getTiltEmoji(entry.tilt_score)} {(entry.tilt_score * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Player Cards */}
      <div className="pressure-player-cards">
        {Object.entries(stats.player_summaries).map(([name, playerStats]) => (
          <div key={name} className="player-card">
            <h5>{name}</h5>
            <div className="signature-move">{playerStats.signature_move}</div>
            
            <div className="player-stats-grid">
              <div className="stat">
                <span className="emoji">🏆</span>
                <span className="value">{playerStats.big_wins}</span>
                <span className="label">Big Wins</span>
              </div>
              <div className="stat">
                <span className="emoji">😭</span>
                <span className="value">{playerStats.big_losses}</span>
                <span className="label">Big Losses</span>
              </div>
              <div className="stat">
                <span className="emoji">🎭</span>
                <span className="value">{playerStats.successful_bluffs}</span>
                <span className="label">Bluffs</span>
              </div>
              <div className="stat">
                <span className="emoji">💔</span>
                <span className="value">{playerStats.bad_beats}</span>
                <span className="label">Bad Beats</span>
              </div>
            </div>
            
            <div className="meters">
              <div className="meter">
                <span className="meter-label">Tilt Level</span>
                <div className="meter-bar">
                  <div 
                    className="meter-fill tilt"
                    style={{ width: `${playerStats.tilt_score * 100}%` }}
                  />
                </div>
                <span className="meter-emoji">{getTiltEmoji(playerStats.tilt_score)}</span>
              </div>
              
              <div className="meter">
                <span className="meter-label">Aggression</span>
                <div className="meter-bar">
                  <div 
                    className="meter-fill aggression"
                    style={{ width: `${playerStats.aggression_score * 100}%` }}
                  />
                </div>
                <span className="meter-emoji">{getAggressionEmoji(playerStats.aggression_score)}</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Fun Facts */}
      {stats.fun_facts.length > 0 && (
        <div className="fun-facts">
          <h4>📊 Game Highlights</h4>
          {stats.fun_facts.map((fact, i) => (
            <div key={i} className="fun-fact">{fact}</div>
          ))}
        </div>
      )}
    </div>
  );
}