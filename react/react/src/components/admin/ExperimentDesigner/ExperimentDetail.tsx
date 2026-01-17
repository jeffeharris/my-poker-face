import { useState, useEffect, useCallback } from 'react';
import {
  RefreshCw,
  Clock,
  CheckCircle,
  XCircle,
  Loader2,
  Trophy,
  Target,
  Percent,
  Timer,
  Gamepad2,
  FlaskConical,
  Filter,
  Zap,
  Monitor,
  Pause,
  Play,
} from 'lucide-react';
import { LiveMonitoringView } from './monitoring';
import { config } from '../../../config';
import type { VariantResultSummary, LiveStats, LatencyMetrics } from './types';

type ExperimentStatus = 'pending' | 'running' | 'completed' | 'failed' | 'paused';

interface ExperimentDetailType {
  id: number;
  name: string;
  description: string;
  hypothesis: string;
  tags: string[];
  status: ExperimentStatus;
  created_at: string;
  completed_at: string | null;
  games_count: number;
  num_tournaments: number;
  model: string | null;
  provider: string | null;
  notes: string | null;
  config: Record<string, unknown>;
  summary: {
    tournaments: number;
    total_hands: number;
    total_api_calls: number;
    total_duration_seconds: number;
    avg_hands_per_tournament: number;
    winners: Record<string, number>;
    variants?: Record<string, VariantResultSummary>;
  } | null;
}

interface ExperimentGame {
  id: number;
  game_id: string;
  variant: string | null;
  variant_config: Record<string, unknown> | null;
  tournament_number: number;
  created_at: string;
}

interface DecisionStats {
  total: number;
  correct: number;
  marginal: number;
  mistake: number;
  correct_pct: number;
  avg_ev_lost: number;
  by_player: Record<string, {
    total: number;
    correct: number;
    correct_pct: number;
    avg_ev_lost: number;
  }>;
}

interface ExperimentDetailProps {
  experimentId: number;
  onBack: () => void;
}

const STATUS_CONFIG: Record<ExperimentStatus, { icon: React.ReactNode; className: string; label: string }> = {
  pending: {
    icon: <Clock size={16} />,
    className: 'status-badge--pending',
    label: 'Pending',
  },
  running: {
    icon: <Loader2 size={16} className="animate-spin" />,
    className: 'status-badge--running',
    label: 'Running',
  },
  completed: {
    icon: <CheckCircle size={16} />,
    className: 'status-badge--completed',
    label: 'Completed',
  },
  failed: {
    icon: <XCircle size={16} />,
    className: 'status-badge--failed',
    label: 'Failed',
  },
  paused: {
    icon: <Pause size={16} />,
    className: 'status-badge--paused',
    label: 'Paused',
  },
};

export function ExperimentDetail({ experimentId, onBack }: ExperimentDetailProps) {
  const [experiment, setExperiment] = useState<ExperimentDetailType | null>(null);
  const [games, setGames] = useState<ExperimentGame[]>([]);
  const [decisionStats, setDecisionStats] = useState<DecisionStats | null>(null);
  const [liveStats, setLiveStats] = useState<LiveStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [variantFilter, setVariantFilter] = useState<string | null>(null);
  const [showMonitor, setShowMonitor] = useState(false);
  const [pauseLoading, setPauseLoading] = useState(false);
  const [resumeLoading, setResumeLoading] = useState(false);

  const fetchExperiment = useCallback(async () => {
    try {
      const [expResponse, gamesResponse] = await Promise.all([
        fetch(`${config.API_URL}/api/experiments/${experimentId}`),
        fetch(`${config.API_URL}/api/experiments/${experimentId}/games`),
      ]);

      const expData = await expResponse.json();
      const gamesData = await gamesResponse.json();

      if (expData.success) {
        setExperiment(expData.experiment);
        setDecisionStats(expData.decision_stats);
        setLiveStats(expData.live_stats || null);
        setError(null);
      } else {
        setError(expData.error || 'Failed to load experiment');
      }

      if (gamesData.success) {
        setGames(gamesData.games);
      }
    } catch (err) {
      console.error('Failed to fetch experiment:', err);
      setError('Failed to connect to server');
    } finally {
      setLoading(false);
    }
  }, [experimentId]);

  // Initial load
  useEffect(() => {
    fetchExperiment();
  }, [fetchExperiment]);

  // Auto-refresh for running experiments
  // Note: cleanup function ensures only one interval runs at a time
  useEffect(() => {
    if (experiment?.status !== 'running') return;

    const interval = setInterval(fetchExperiment, 5000);
    return () => clearInterval(interval);
  }, [experiment?.status, fetchExperiment]);

  const handlePause = async () => {
    setPauseLoading(true);
    try {
      const response = await fetch(`${config.API_URL}/api/experiments/${experimentId}/pause`, {
        method: 'POST',
      });
      const data = await response.json();
      if (data.success) {
        // Refresh to get updated status
        fetchExperiment();
      } else {
        setError(data.error || 'Failed to pause experiment');
      }
    } catch (err) {
      console.error('Failed to pause experiment:', err);
      setError('Failed to pause experiment');
    } finally {
      setPauseLoading(false);
    }
  };

  const handleResume = async () => {
    setResumeLoading(true);
    try {
      const response = await fetch(`${config.API_URL}/api/experiments/${experimentId}/resume`, {
        method: 'POST',
      });
      const data = await response.json();
      if (data.success) {
        // Refresh to get updated status
        fetchExperiment();
      } else {
        setError(data.error || 'Failed to resume experiment');
      }
    } catch (err) {
      console.error('Failed to resume experiment:', err);
      setError('Failed to resume experiment');
    } finally {
      setResumeLoading(false);
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const formatDuration = (seconds: number) => {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  };

  const formatLatency = (ms: number) => {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };

  if (loading) {
    return (
      <div className="experiment-detail__loading">
        <Loader2 size={24} className="animate-spin" />
        <span>Loading experiment...</span>
      </div>
    );
  }

  if (error || !experiment) {
    return (
      <div className="experiment-detail__error">
        <XCircle size={24} />
        <span>{error || 'Experiment not found'}</span>
        <button onClick={onBack} type="button">
          Go Back
        </button>
      </div>
    );
  }

  const statusConfig = STATUS_CONFIG[experiment.status];
  const summary = experiment.summary;

  return (
    <div className="experiment-detail">
      {/* Header */}
      <div className="experiment-detail__header">
        <div className="experiment-detail__header-main">
          <h2 className="experiment-detail__name">{experiment.name}</h2>
          <span className={`status-badge ${statusConfig.className}`}>
            {statusConfig.icon}
            {statusConfig.label}
          </span>
        </div>
        {experiment.description && (
          <p className="experiment-detail__description">{experiment.description}</p>
        )}
        {experiment.hypothesis && (
          <p className="experiment-detail__hypothesis">
            <strong>Hypothesis:</strong> {experiment.hypothesis}
          </p>
        )}
        <button
          className="experiment-detail__refresh-btn"
          onClick={fetchExperiment}
          type="button"
          title="Refresh"
        >
          <RefreshCw size={16} />
        </button>
        {experiment.status === 'running' && (
          <>
            <button
              className="experiment-detail__monitor-btn"
              onClick={() => setShowMonitor(true)}
              type="button"
              title="Open Live Monitor"
            >
              <Monitor size={16} />
              Live Monitor
            </button>
            <button
              className="experiment-detail__pause-btn"
              onClick={handlePause}
              type="button"
              disabled={pauseLoading}
              title="Pause Experiment"
            >
              {pauseLoading ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Pause size={16} />
              )}
              {pauseLoading ? 'Pausing...' : 'Pause'}
            </button>
          </>
        )}
        {experiment.status === 'paused' && (
          <button
            className="experiment-detail__resume-btn"
            onClick={handleResume}
            type="button"
            disabled={resumeLoading}
            title="Resume Experiment"
          >
            {resumeLoading ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Play size={16} />
            )}
            {resumeLoading ? 'Resuming...' : 'Resume'}
          </button>
        )}
      </div>

      {/* Summary Stats */}
      {summary && (
        <div className="experiment-detail__summary">
          <div className="experiment-detail__stat">
            <Gamepad2 size={20} />
            <div className="experiment-detail__stat-content">
              <span className="experiment-detail__stat-value">{summary.tournaments}</span>
              <span className="experiment-detail__stat-label">Tournaments</span>
            </div>
          </div>

          <div className="experiment-detail__stat">
            <Target size={20} />
            <div className="experiment-detail__stat-content">
              <span className="experiment-detail__stat-value">{summary.total_hands}</span>
              <span className="experiment-detail__stat-label">Total Hands</span>
            </div>
          </div>

          <div className="experiment-detail__stat">
            <Timer size={20} />
            <div className="experiment-detail__stat-content">
              <span className="experiment-detail__stat-value">
                {formatDuration(summary.total_duration_seconds)}
              </span>
              <span className="experiment-detail__stat-label">Duration</span>
            </div>
          </div>

          {decisionStats && decisionStats.total > 0 && (
            <div className="experiment-detail__stat">
              <Percent size={20} />
              <div className="experiment-detail__stat-content">
                <span className="experiment-detail__stat-value">{decisionStats.correct_pct}%</span>
                <span className="experiment-detail__stat-label">Correct Decisions</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Live Variant Stats (for A/B tests with real-time data) */}
      {liveStats && Object.keys(liveStats.by_variant).length > 0 && (
        <div className="experiment-detail__section">
          <h3 className="experiment-detail__section-title">
            <FlaskConical size={18} />
            Variant Comparison
            {experiment.status === 'running' && (
              <span className="experiment-detail__live-indicator">Live</span>
            )}
          </h3>
          <div className="experiment-detail__variant-comparison">
            {Object.entries(liveStats.by_variant).map(([label, variantLive]) => {
              // Get model info from summary if available
              const variantSummary = summary?.variants?.[label];
              return (
                <div key={label} className="experiment-detail__variant-card">
                  <div className="experiment-detail__variant-header">
                    <h4 className="experiment-detail__variant-label">{label}</h4>
                    {variantSummary?.model_config && (
                      <span className="experiment-detail__variant-model">
                        {variantSummary.model_config.provider}/{variantSummary.model_config.model}
                      </span>
                    )}
                  </div>

                  {/* Progress Section */}
                  <div className="experiment-detail__variant-section">
                    <span className="experiment-detail__variant-section-label">Progress</span>
                    <div className="experiment-detail__progress-bar-container">
                      <div
                        className="experiment-detail__progress-bar"
                        style={{ width: `${variantLive.progress.progress_pct}%` }}
                      />
                    </div>
                    <span className="experiment-detail__progress-text">
                      {variantLive.progress.current_hands}/{variantLive.progress.max_hands} hands ({variantLive.progress.progress_pct}%)
                    </span>
                    <span className="experiment-detail__progress-games">
                      {variantLive.progress.games_count}/{variantLive.progress.games_expected} tournaments
                    </span>
                  </div>

                  {/* Decision Quality Section */}
                  {variantLive.decision_quality && (
                    <div className="experiment-detail__variant-section">
                      <span className="experiment-detail__variant-section-label">Decision Quality</span>
                      <div className="experiment-detail__decision-row">
                        <span className="experiment-detail__decision-metric experiment-detail__decision-metric--correct">
                          {variantLive.decision_quality.correct_pct}% Correct
                        </span>
                        <span className="experiment-detail__decision-metric experiment-detail__decision-metric--mistake">
                          {variantLive.decision_quality.mistakes} Mistakes
                        </span>
                        <span className="experiment-detail__decision-metric">
                          ${variantLive.decision_quality.avg_ev_lost} EV
                        </span>
                      </div>
                    </div>
                  )}

                  {/* API Latency Section */}
                  {variantLive.latency_metrics && (
                    <div className="experiment-detail__variant-section">
                      <span className="experiment-detail__variant-section-label">
                        <Zap size={12} />
                        API Latency
                      </span>
                      <div className="experiment-detail__latency-grid">
                        <div className="experiment-detail__latency-cell">
                          <span className="experiment-detail__latency-label">Avg</span>
                          <span className="experiment-detail__latency-value">
                            {formatLatency(variantLive.latency_metrics.avg_ms)}
                          </span>
                        </div>
                        <div className="experiment-detail__latency-cell">
                          <span className="experiment-detail__latency-label">P50</span>
                          <span className="experiment-detail__latency-value">
                            {formatLatency(variantLive.latency_metrics.p50_ms)}
                          </span>
                        </div>
                        <div className="experiment-detail__latency-cell">
                          <span className="experiment-detail__latency-label">P95</span>
                          <span className="experiment-detail__latency-value">
                            {formatLatency(variantLive.latency_metrics.p95_ms)}
                          </span>
                        </div>
                        <div className="experiment-detail__latency-cell">
                          <span className="experiment-detail__latency-label">P99</span>
                          <span className="experiment-detail__latency-value">
                            {formatLatency(variantLive.latency_metrics.p99_ms)}
                          </span>
                        </div>
                      </div>
                      <span className="experiment-detail__latency-count">
                        {variantLive.latency_metrics.count.toLocaleString()} API calls
                      </span>
                    </div>
                  )}

                  {/* Winners (from summary if available) */}
                  {variantSummary && Object.keys(variantSummary.winners).length > 0 && (
                    <div className="experiment-detail__variant-winners">
                      <span className="experiment-detail__variant-winners-label">Top winners:</span>
                      {Object.entries(variantSummary.winners)
                        .sort(([, a], [, b]) => b - a)
                        .slice(0, 3)
                        .map(([name, wins]) => (
                          <span key={name} className="experiment-detail__variant-winner">
                            {name}: {wins}
                          </span>
                        ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Winners Distribution */}
      {summary?.winners && Object.keys(summary.winners).length > 0 && (
        <div className="experiment-detail__section">
          <h3 className="experiment-detail__section-title">
            <Trophy size={18} />
            Winner Distribution
          </h3>
          <div className="experiment-detail__winners">
            {Object.entries(summary.winners)
              .sort(([, a], [, b]) => b - a)
              .map(([name, wins]) => {
                const winPct = summary.tournaments > 0 ? (wins / summary.tournaments) * 100 : 0;
                return (
                  <div key={name} className="experiment-detail__winner">
                    <span className="experiment-detail__winner-name">{name}</span>
                    <div className="experiment-detail__winner-bar-container">
                      <div
                        className="experiment-detail__winner-bar"
                        style={{ width: `${winPct}%` }}
                      />
                    </div>
                    <span className="experiment-detail__winner-count">
                      {wins} ({Math.round(winPct)}%)
                    </span>
                  </div>
                );
              })}
          </div>
        </div>
      )}

      {/* Decision Quality */}
      {decisionStats && decisionStats.total > 0 && (
        <div className="experiment-detail__section">
          <h3 className="experiment-detail__section-title">
            <Target size={18} />
            Decision Quality
          </h3>
          <div className="experiment-detail__decision-stats">
            <div className="experiment-detail__decision-overview">
              <div className="experiment-detail__decision-bar">
                <div
                  className="experiment-detail__decision-bar-correct"
                  style={{ width: `${decisionStats.correct_pct}%` }}
                  title={`Correct: ${decisionStats.correct}`}
                />
                <div
                  className="experiment-detail__decision-bar-marginal"
                  style={{ width: `${(decisionStats.marginal / decisionStats.total) * 100}%` }}
                  title={`Marginal: ${decisionStats.marginal}`}
                />
                <div
                  className="experiment-detail__decision-bar-mistake"
                  style={{ width: `${(decisionStats.mistake / decisionStats.total) * 100}%` }}
                  title={`Mistake: ${decisionStats.mistake}`}
                />
              </div>
              <div className="experiment-detail__decision-legend">
                <span className="experiment-detail__legend-item experiment-detail__legend-item--correct">
                  Correct: {decisionStats.correct} ({decisionStats.correct_pct}%)
                </span>
                <span className="experiment-detail__legend-item experiment-detail__legend-item--marginal">
                  Marginal: {decisionStats.marginal}
                </span>
                <span className="experiment-detail__legend-item experiment-detail__legend-item--mistake">
                  Mistake: {decisionStats.mistake}
                </span>
              </div>
            </div>

            {decisionStats.by_player && Object.keys(decisionStats.by_player).length > 0 && (
              <div className="experiment-detail__player-stats">
                <h4>By Player</h4>
                <table className="experiment-detail__player-table">
                  <thead>
                    <tr>
                      <th>Player</th>
                      <th>Decisions</th>
                      <th>Correct %</th>
                      <th>Avg EV Lost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(decisionStats.by_player)
                      .sort(([, a], [, b]) => b.correct_pct - a.correct_pct)
                      .map(([name, stats]) => (
                        <tr key={name}>
                          <td>{name}</td>
                          <td>{stats.total}</td>
                          <td>{stats.correct_pct}%</td>
                          <td>${stats.avg_ev_lost}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Games List */}
      {games.length > 0 && (
        <div className="experiment-detail__section">
          <h3 className="experiment-detail__section-title">
            <Gamepad2 size={18} />
            Tournament Games ({games.length})
            {/* Variant filter dropdown */}
            {summary?.variants && Object.keys(summary.variants).length > 0 && (
              <div className="experiment-detail__variant-filter">
                <Filter size={14} />
                <select
                  value={variantFilter || ''}
                  onChange={(e) => setVariantFilter(e.target.value || null)}
                  className="experiment-detail__variant-filter-select"
                >
                  <option value="">All variants</option>
                  {Object.keys(summary.variants).map((label) => (
                    <option key={label} value={label}>{label}</option>
                  ))}
                </select>
              </div>
            )}
          </h3>
          <div className="experiment-detail__games">
            {games
              .filter((game) => !variantFilter || game.variant === variantFilter)
              .map((game) => (
                <div key={game.id} className="experiment-detail__game">
                  <span className="experiment-detail__game-number">
                    #{game.tournament_number}
                  </span>
                  {game.variant && (
                    <span className="experiment-detail__game-variant">
                      {game.variant}
                    </span>
                  )}
                  <span className="experiment-detail__game-id">{game.game_id}</span>
                  <span className="experiment-detail__game-date">
                    {formatDate(game.created_at)}
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Config */}
      <div className="experiment-detail__section">
        <h3 className="experiment-detail__section-title">Configuration</h3>
        <pre className="experiment-detail__config">
          {JSON.stringify(experiment.config, null, 2)}
        </pre>
      </div>

      {/* Timestamps */}
      <div className="experiment-detail__timestamps">
        <span>Created: {formatDate(experiment.created_at)}</span>
        {experiment.completed_at && (
          <span>Completed: {formatDate(experiment.completed_at)}</span>
        )}
      </div>

      {/* Live Monitor Overlay */}
      {showMonitor && (
        <LiveMonitoringView
          experimentId={experimentId}
          experimentName={experiment.name}
          onClose={() => setShowMonitor(false)}
        />
      )}
    </div>
  );
}

export default ExperimentDetail;
