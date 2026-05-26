import { memo, type ReactNode } from 'react';
import {
  Target,
  Flame,
  Square,
  Phone,
  Search,
  Frown,
  Angry,
  Meh,
  Smile,
  type LucideIcon,
} from 'lucide-react';
import type { Player } from '../../types';
import { useDisplayNickname } from '../../stores/nicknameOverridesStore';
import './HeadsUpOpponentPanel.css';

interface HeadsUpOpponentPanelProps {
  opponent: Player;
  gameId?: string;
  humanPlayerName?: string;
}

export const HeadsUpOpponentPanel = memo(function HeadsUpOpponentPanel({
  opponent,
}: HeadsUpOpponentPanelProps) {
  // Observation and pressure summary now flow through the socket game-state
  // payload (see flask_app/handlers/game_handler.update_and_emit_game_state).
  // Previously this component polled admin-only debug routes every 5s, which
  // returned 401 for non-admin players in heads-up mode.
  const observation = opponent.observation;
  const opponentStats = opponent.pressure_summary;
  const psych = opponent.psychology;
  const displayNickname = useDisplayNickname();

  const getPlayStyleLabel = (style: string): { label: string; icon: LucideIcon } => {
    const labels: Record<string, { label: string; icon: LucideIcon }> = {
      'tight-aggressive': { label: 'Tight & Aggressive', icon: Target },
      'loose-aggressive': { label: 'Loose & Aggressive', icon: Flame },
      'tight-passive': { label: 'Tight & Passive', icon: Square },
      'loose-passive': { label: 'Calling Station', icon: Phone },
      unknown: { label: 'Still reading...', icon: Search },
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
    const iconProps = { size: 16, className: 'tilt-icon' };
    switch (category) {
      case 'severe':
        return <Frown {...iconProps} />;
      case 'moderate':
        return <Angry {...iconProps} />;
      case 'mild':
        return <Meh {...iconProps} />;
      default:
        return <Smile {...iconProps} />;
    }
  };

  const getTiltDescription = (category: string, source?: string, losingStreak?: number) => {
    if (category === 'none') return 'Cool and collected';

    const sourceDescriptions: Record<string, string> = {
      bad_beat: 'Frustrated after bad beat',
      bluff_called: 'Rattled - bluff got called',
      big_loss: 'Stinging from big loss',
      losing_streak: `On a ${losingStreak || 0} hand losing streak`,
      revenge: 'Playing for revenge',
    };

    return (
      sourceDescriptions[source || ''] ||
      `${category.charAt(0).toUpperCase() + category.slice(1)} tilt`
    );
  };

  const playStyle = observation ? getPlayStyleLabel(observation.play_style) : null;

  return (
    <div className="heads-up-opponent-panel" data-testid="heads-up-panel">
      {/* Reading Header */}
      <div className="panel-header">Reading {displayNickname(opponent)}...</div>

      {/* Play Style - Primary observation (threshold synced with poker/config.py MIN_HANDS_FOR_SUMMARY) */}
      {observation && observation.hands_observed >= 10 ? (
        <div className="psychology-section playstyle-section">
          <div className="playstyle-main">
            {playStyle && <playStyle.icon className="playstyle-icon" size={18} />}
            <span className="playstyle-label">{playStyle?.label}</span>
          </div>
          <div className="playstyle-details">
            <span className="detail-item">{getAggressionLabel(observation.aggression_factor)}</span>
            <span className="detail-separator">•</span>
            <span className="detail-item">{Math.round(observation.vpip * 100)}% VPIP</span>
          </div>
          <div className="hands-observed">{observation.hands_observed} hands observed</div>
        </div>
      ) : (
        <div className="psychology-section playstyle-section">
          <div className="playstyle-main">
            <Search className="playstyle-icon" size={18} />
            <span className="playstyle-label">Still reading...</span>
          </div>
          <div className="hands-observed">{observation?.hands_observed || 0} hands observed</div>
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
          {psych.narrative && <div className="emotional-narrative">{psych.narrative}</div>}
          {psych.inner_voice && <div className="inner-voice">"{psych.inner_voice}"</div>}
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
            <div className="tilt-meter-fill" style={{ width: `${psych.tilt_level * 100}%` }} />
          </div>
        </div>
      )}

      {/* Calm state - only show if no other emotional content */}
      {!psych?.narrative && !psych?.inner_voice && (!psych || psych.tilt_category === 'none') && (
        <div className="psychology-section calm-section">
          <Smile className="calm-icon" size={16} />
          <span className="calm-text">Playing steady</span>
        </div>
      )}
    </div>
  );
});
