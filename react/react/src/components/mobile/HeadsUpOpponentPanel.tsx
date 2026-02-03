import { memo, useState, useEffect, type ReactNode } from 'react';
import { Target, Flame, Square, Phone, Search, Frown, Angry, Meh, Smile, type LucideIcon } from 'lucide-react';
import type { Player } from '../../types';
import { logger } from '../../utils/logger';
import { config } from '../../config';
import './HeadsUpOpponentPanel.css';

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
  headsup_wins: number;
  headsup_losses: number;
}

interface OpponentObservation {
  hands_observed: number;
  vpip: number;
  pfr: number;
  aggression_factor: number;
  play_style: string;
  summary: string;
}

interface HeadsUpOpponentPanelProps {
  opponent: Player;
  gameId: string;
  humanPlayerName?: string;
}

export const HeadsUpOpponentPanel = memo(function HeadsUpOpponentPanel({ opponent, gameId, humanPlayerName }: HeadsUpOpponentPanelProps) {
  const [opponentStats, setOpponentStats] = useState<PlayerSummary | null>(null);
  const [observation, setObservation] = useState<OpponentObservation | null>(null);

  // Fetch pressure stats and opponent observations
  useEffect(() => {
    if (!gameId) return;

    let mounted = true;
    let timerId: ReturnType<typeof setTimeout>;
    let delayMs = 5000;

    const fetchData = async () => {
      let rateLimited = false;
      try {
        // Fetch pressure stats
        const statsResponse = await fetch(`${config.API_URL}/api/game/${gameId}/pressure-stats`);
        if (statsResponse.status === 429) {
          rateLimited = true;
        } else if (!statsResponse.ok) {
          logger.error(`Pressure stats returned ${statsResponse.status}`);
        } else if (mounted) {
          const data = await statsResponse.json();
          const stats = data.player_summaries?.[opponent.name];
          if (stats) {
            setOpponentStats(stats);
          }
        }

        // Fetch opponent observations from memory debug
        const memoryResponse = await fetch(`${config.API_URL}/api/game/${gameId}/memory-debug`);
        if (memoryResponse.status === 429) {
          rateLimited = true;
        } else if (!memoryResponse.ok) {
          logger.error(`Memory debug returned ${memoryResponse.status}`);
        } else if (mounted) {
          const memoryData = await memoryResponse.json();
          const opponentModels = memoryData.opponent_models || {};

          // First try to find human player's observations about this opponent
          let found = false;
          if (humanPlayerName && opponentModels[humanPlayerName]?.[opponent.name]) {
            const obs = opponentModels[humanPlayerName][opponent.name];
            if (obs.hands_observed > 0) {
              setObservation(obs);
              found = true;
            }
          }

          if (!found) {
            // Fallback: find observations about our opponent from any observer
            for (const observer of Object.keys(opponentModels)) {
              const obs = opponentModels[observer]?.[opponent.name];
              if (obs && obs.hands_observed > 0) {
                setObservation(obs);
                break;
              }
            }
          }
        }

        delayMs = rateLimited ? Math.min(delayMs * 2, 60000) : 5000;
      } catch (error) {
        logger.error('Failed to fetch opponent data:', error);
      }

      if (mounted) {
        timerId = setTimeout(fetchData, delayMs);
      }
    };

    fetchData();

    return () => {
      mounted = false;
      clearTimeout(timerId);
    };
  }, [gameId, opponent.name, humanPlayerName]);

  const psych = opponent.psychology;

  const getPlayStyleLabel = (style: string): { label: string; icon: LucideIcon } => {
    const labels: Record<string, { label: string; icon: LucideIcon }> = {
      'tight-aggressive': { label: 'Tight & Aggressive', icon: Target },
      'loose-aggressive': { label: 'Loose & Aggressive', icon: Flame },
      'tight-passive': { label: 'Tight & Passive', icon: Square },
      'loose-passive': { label: 'Calling Station', icon: Phone },
      'unknown': { label: 'Still reading...', icon: Search },
    };
    return labels[style] || labels['unknown'];
  };

  const getAggressionLabel = (factor: number) => {
    if (factor > 2) return 'Very aggressive';
    if (factor > 1.5) return 'Aggressive';
    if (factor > 0.8) return 'Balanced';
    if (factor > 0.4) return 'Passive';
    return 'Very passive';
  };

  const getTiltIcon = (category: string): ReactNode => {
    const iconProps = { size: 16, className: "tilt-icon" };
    switch (category) {
      case 'severe': return <Frown {...iconProps} />;
      case 'moderate': return <Angry {...iconProps} />;
      case 'mild': return <Meh {...iconProps} />;
      default: return <Smile {...iconProps} />;
    }
  };

  const getTiltDescription = (category: string, source?: string, losingStreak?: number) => {
    if (category === 'none') return 'Cool and collected';

    const sourceDescriptions: Record<string, string> = {
      'bad_beat': 'Frustrated after bad beat',
      'bluff_called': 'Rattled - bluff got called',
      'big_loss': 'Stinging from big loss',
      'losing_streak': `On a ${losingStreak || 0} hand losing streak`,
      'revenge': 'Playing for revenge',
    };

    return sourceDescriptions[source || ''] || `${category.charAt(0).toUpperCase() + category.slice(1)} tilt`;
  };

  const playStyle = observation ? getPlayStyleLabel(observation.play_style) : null;

  return (
    <div className="heads-up-opponent-panel" data-testid="heads-up-panel">
      {/* Reading Header */}
      <div className="panel-header">
        Reading {opponent.nickname || opponent.name}...
      </div>

      {/* Play Style - Primary observation (threshold synced with poker/config.py MIN_HANDS_FOR_SUMMARY) */}
      {observation && observation.hands_observed >= 10 ? (
        <div className="psychology-section playstyle-section">
          <div className="playstyle-main">
            {playStyle && <playStyle.icon className="playstyle-icon" size={18} />}
            <span className="playstyle-label">{playStyle?.label}</span>
          </div>
          <div className="playstyle-details">
            <span className="detail-item">
              {getAggressionLabel(observation.aggression_factor)}
            </span>
            <span className="detail-separator">•</span>
            <span className="detail-item">
              {Math.round(observation.vpip * 100)}% VPIP
            </span>
          </div>
          <div className="hands-observed">
            {observation.hands_observed} hands observed
          </div>
        </div>
      ) : (
        <div className="psychology-section playstyle-section">
          <div className="playstyle-main">
            <Search className="playstyle-icon" size={18} />
            <span className="playstyle-label">Still reading...</span>
          </div>
          <div className="hands-observed">
            {observation?.hands_observed || 0} hands observed
          </div>
        </div>
      )}

      {/* Heads-Up Record */}
      <div className="psychology-section stats-section">
        <div className="section-label">Heads-Up Record</div>
        <div className="session-record">
          <span className="wins">{opponentStats?.headsup_wins || 0}W</span>
          <span className="separator"> - </span>
          <span className="losses">{opponentStats?.headsup_losses || 0}L</span>
        </div>
        {opponentStats && opponentStats.biggest_pot_won > 0 && (
          <div className="biggest-pot">Best pot: ${opponentStats.biggest_pot_won}</div>
        )}
        {opponentStats?.signature_move && (
          <div className="signature-move">{opponentStats.signature_move}</div>
        )}
      </div>

      {/* Emotional State - from psychology */}
      {(psych?.narrative || psych?.inner_voice) && (
        <div className="psychology-section emotional-section">
          {psych.narrative && (
            <div className="emotional-narrative">{psych.narrative}</div>
          )}
          {psych.inner_voice && (
            <div className="inner-voice">"{psych.inner_voice}"</div>
          )}
        </div>
      )}

      {/* Tilt Status */}
      {psych && psych.tilt_category !== 'none' && (
        <div className={`psychology-section tilt-section ${psych.tilt_category}`}>
          <div className="tilt-header">
            <span className="tilt-emoji">{getTiltIcon(psych.tilt_category)}</span>
            <span className="tilt-label">
              {getTiltDescription(psych.tilt_category, psych.tilt_source, psych.losing_streak)}
            </span>
          </div>
          <div className="tilt-meter">
            <div
              className="tilt-meter-fill"
              style={{ width: `${psych.tilt_level * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* Calm state - only show if no other emotional content */}
      {(!psych?.narrative && !psych?.inner_voice && (!psych || psych.tilt_category === 'none')) && (
        <div className="psychology-section calm-section">
          <Smile className="calm-icon" size={16} />
          <span className="calm-text">Playing steady</span>
        </div>
      )}
    </div>
  );
});
