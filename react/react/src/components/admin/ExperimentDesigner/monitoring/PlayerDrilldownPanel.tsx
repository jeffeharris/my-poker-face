/**
 * PlayerDrilldownPanel - Slide-out panel with detailed player info
 *
 * Shows psychology, tilt, play style, LLM stats, and recent decisions.
 */

import { useState, useEffect } from 'react';
import { X, Loader2, Brain, Flame, Target, Cpu, History } from 'lucide-react';
import { config } from '../../../../config';
import type { PlayerDetail, PlayerDetailResponse } from './types';

interface PlayerDrilldownPanelProps {
  experimentId: number;
  gameId: string;
  playerName: string;
  onClose: () => void;
}

export function PlayerDrilldownPanel({
  experimentId,
  gameId,
  playerName,
  onClose,
}: PlayerDrilldownPanelProps) {
  const [playerDetail, setPlayerDetail] = useState<PlayerDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchPlayerDetail = async () => {
      try {
        const response = await fetch(
          `${config.API_URL}/api/experiments/${experimentId}/games/${gameId}/player/${encodeURIComponent(playerName)}`
        );
        const data: PlayerDetailResponse = await response.json();

        if (data.success) {
          setPlayerDetail(data);
          setError(null);
        } else {
          setError(data.error || 'Failed to load player details');
        }
      } catch (err) {
        console.error('Failed to fetch player detail:', err);
        setError('Failed to connect to server');
      } finally {
        setLoading(false);
      }
    };

    fetchPlayerDetail();
  }, [experimentId, gameId, playerName]);

  // Get tilt color based on level
  const getTiltColor = (level: number): string => {
    if (level < 20) return 'var(--color-success)';
    if (level < 40) return 'var(--color-warning)';
    if (level < 70) return '#ff9800';
    return 'var(--color-error)';
  };

  // Get tilt label based on category
  const getTiltLabel = (category: string): string => {
    switch (category) {
      case 'none':
        return 'Calm';
      case 'mild':
        return 'Mild';
      case 'moderate':
        return 'Moderate';
      case 'severe':
        return 'Severe';
      default:
        return category;
    }
  };

  // Get decision quality color
  const getDecisionColor = (quality: string): string => {
    switch (quality) {
      case 'correct':
        return 'var(--color-success)';
      case 'marginal':
        return 'var(--color-warning)';
      case 'mistake':
        return 'var(--color-error)';
      default:
        return 'var(--color-text-secondary)';
    }
  };

  // Format latency
  const formatLatency = (ms: number): string => {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };

  return (
    <div className="player-drilldown">
      {/* Header */}
      <header className="player-drilldown__header">
        <h2 className="player-drilldown__title">{playerName}</h2>
        <button
          className="player-drilldown__close"
          onClick={onClose}
          type="button"
          title="Close (ESC)"
        >
          <X size={20} />
        </button>
      </header>

      {/* Content */}
      <div className="player-drilldown__content">
        {loading && (
          <div className="player-drilldown__loading">
            <Loader2 size={24} className="animate-spin" />
            <span>Loading player details...</span>
          </div>
        )}

        {error && (
          <div className="player-drilldown__error">
            <span>{error}</span>
          </div>
        )}

        {playerDetail && !loading && (
          <>
            {/* Stack Info */}
            <div className="player-drilldown__stack">
              Stack: ${playerDetail.player.stack.toLocaleString()}
            </div>

            {/* Psychology Section - shows disabled message if not enabled */}
            {!playerDetail.psychology_enabled ? (
              <section className="player-drilldown__section">
                <h3 className="player-drilldown__section-title">
                  <Brain size={16} />
                  Psychology
                </h3>
                <p className="player-drilldown__disabled">
                  Psychology tracking is disabled for this experiment variant.
                  Enable <code>enable_psychology: true</code> in the experiment config to track emotional state and tilt.
                </p>
              </section>
            ) : (
              <>
                {/* Emotional State Section */}
                <section className="player-drilldown__section">
                  <h3 className="player-drilldown__section-title">
                    <Brain size={16} />
                    Emotional State
                  </h3>
                  {playerDetail.psychology.narrative ? (
                    <p className="player-drilldown__narrative">
                      {playerDetail.psychology.narrative}
                    </p>
                  ) : (
                    <p className="player-drilldown__empty">No emotional narrative available</p>
                  )}
                  {playerDetail.psychology.inner_voice && (
                    <blockquote className="player-drilldown__inner-voice">
                      "{playerDetail.psychology.inner_voice}"
                    </blockquote>
                  )}
                </section>

                {/* Tilt Section */}
                <section className="player-drilldown__section">
                  <h3 className="player-drilldown__section-title">
                    <Flame size={16} />
                    Tilt Level
                  </h3>
                  <div className="player-drilldown__tilt">
                    <div className="player-drilldown__tilt-bar">
                      <div
                        className="player-drilldown__tilt-fill"
                        style={{
                          width: `${playerDetail.psychology.tilt_level}%`,
                          backgroundColor: getTiltColor(playerDetail.psychology.tilt_level),
                        }}
                      />
                    </div>
                    <div className="player-drilldown__tilt-info">
                      <span className="player-drilldown__tilt-percentage">
                        {playerDetail.psychology.tilt_level}%
                      </span>
                      <span className="player-drilldown__tilt-category">
                        ({getTiltLabel(playerDetail.psychology.tilt_category)})
                      </span>
                    </div>
                    {playerDetail.psychology.tilt_source && (
                      <span className="player-drilldown__tilt-source">
                        Source: {playerDetail.psychology.tilt_source.replace(/_/g, ' ')}
                      </span>
                    )}
                  </div>
                </section>
              </>
            )}

            {/* Play Style Section */}
            {playerDetail.play_style && playerDetail.play_style.hands_observed > 0 && (
              <section className="player-drilldown__section">
                <h3 className="player-drilldown__section-title">
                  <Target size={16} />
                  Play Style
                </h3>
                <div className="player-drilldown__play-style">
                  <span className="player-drilldown__style-summary">
                    {playerDetail.play_style.summary.split('-').map(
                      word => word.charAt(0).toUpperCase() + word.slice(1)
                    ).join('-')}
                  </span>
                  <div className="player-drilldown__style-stats">
                    <div className="player-drilldown__style-stat">
                      <span className="player-drilldown__style-label">VPIP</span>
                      <span className="player-drilldown__style-value">
                        {playerDetail.play_style.vpip}%
                      </span>
                    </div>
                    <div className="player-drilldown__style-stat">
                      <span className="player-drilldown__style-label">PFR</span>
                      <span className="player-drilldown__style-value">
                        {playerDetail.play_style.pfr}%
                      </span>
                    </div>
                    <div className="player-drilldown__style-stat">
                      <span className="player-drilldown__style-label">AF</span>
                      <span className="player-drilldown__style-value">
                        {playerDetail.play_style.aggression_factor}
                      </span>
                    </div>
                  </div>
                  <span className="player-drilldown__hands-observed">
                    ({playerDetail.play_style.hands_observed} hands observed)
                  </span>
                </div>
              </section>
            )}

            {/* LLM Stats Section */}
            {playerDetail.llm_debug && playerDetail.llm_debug.total_calls && (
              <section className="player-drilldown__section">
                <h3 className="player-drilldown__section-title">
                  <Cpu size={16} />
                  LLM Stats
                </h3>
                <div className="player-drilldown__llm">
                  <div className="player-drilldown__llm-model">
                    <span className="player-drilldown__llm-provider">
                      {playerDetail.llm_debug.provider}
                    </span>
                    <span className="player-drilldown__llm-model-name">
                      {playerDetail.llm_debug.model}
                    </span>
                    {playerDetail.llm_debug.reasoning_effort && (
                      <span className="player-drilldown__llm-reasoning">
                        reasoning: {playerDetail.llm_debug.reasoning_effort}
                      </span>
                    )}
                  </div>
                  <div className="player-drilldown__llm-stats">
                    <div className="player-drilldown__llm-stat">
                      <span className="player-drilldown__llm-label">Latency</span>
                      <span className="player-drilldown__llm-value">
                        {formatLatency(playerDetail.llm_debug.avg_latency_ms || 0)} avg
                      </span>
                    </div>
                    {playerDetail.llm_debug.p95_latency_ms && (
                      <div className="player-drilldown__llm-stat">
                        <span className="player-drilldown__llm-label">P95</span>
                        <span className="player-drilldown__llm-value">
                          {formatLatency(playerDetail.llm_debug.p95_latency_ms)}
                        </span>
                      </div>
                    )}
                    <div className="player-drilldown__llm-stat">
                      <span className="player-drilldown__llm-label">Cost</span>
                      <span className="player-drilldown__llm-value">
                        ${playerDetail.llm_debug.avg_cost_per_call?.toFixed(4)}/call
                      </span>
                    </div>
                    <div className="player-drilldown__llm-stat">
                      <span className="player-drilldown__llm-label">Calls</span>
                      <span className="player-drilldown__llm-value">
                        {playerDetail.llm_debug.total_calls}
                      </span>
                    </div>
                  </div>
                </div>
              </section>
            )}

            {/* Recent Decisions Section */}
            {playerDetail.recent_decisions && playerDetail.recent_decisions.length > 0 && (
              <section className="player-drilldown__section">
                <h3 className="player-drilldown__section-title">
                  <History size={16} />
                  Recent Decisions
                </h3>
                <table className="player-drilldown__decisions-table">
                  <thead>
                    <tr>
                      <th>H#</th>
                      <th>Phase</th>
                      <th>Action</th>
                      <th>Quality</th>
                      <th>EV</th>
                    </tr>
                  </thead>
                  <tbody>
                    {playerDetail.recent_decisions.map((decision, idx) => (
                      <tr key={idx}>
                        <td>{decision.hand_number}</td>
                        <td>{decision.phase?.replace(/_/g, ' ')}</td>
                        <td>{decision.action}</td>
                        <td>
                          <span
                            className="player-drilldown__quality-badge"
                            style={{ color: getDecisionColor(decision.decision_quality) }}
                          >
                            {decision.decision_quality === 'correct' && '✓'}
                            {decision.decision_quality === 'marginal' && '⚠'}
                            {decision.decision_quality === 'mistake' && '✗'}
                            {' '}{decision.decision_quality}
                          </span>
                        </td>
                        <td>
                          {decision.ev_lost !== null && decision.ev_lost > 0
                            ? `-$${decision.ev_lost}`
                            : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
