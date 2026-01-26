import { useState, useEffect, useCallback } from 'react';
import {
  RefreshCw,
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
  Play,
  Pause,
  DollarSign,
  XCircle,
  Archive,
  ArchiveRestore,
  AlertTriangle,
  Wand2,
  ChevronDown,
  ChevronRight,
  MessageSquare,
  Brain,
  Lightbulb,
  FlaskRound,
} from 'lucide-react';
import { LiveMonitoringView } from './monitoring';
import { config } from '../../../config';
import { formatDate, formatLatency, formatCost } from '../../../utils/formatters';
import { STATUS_CONFIG_LARGE as STATUS_CONFIG, type ExperimentStatus } from './experimentStatus';
import type { VariantResultSummary, LiveStats, FailedTournament, ExperimentConfig, NextStepSuggestion } from './types';

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
  error_message?: string | null;
  config: ExperimentConfig;
  summary: {
    tournaments: number;
    total_hands: number;
    total_api_calls: number;
    total_duration_seconds: number;
    avg_hands_per_tournament: number;
    winners: Record<string, number>;
    variants?: Record<string, VariantResultSummary>;
    failed_tournaments?: FailedTournament[];
    ai_interpretation?: {
      summary: string;
      verdict: string;
      surprises: string[];
      next_steps: (string | NextStepSuggestion)[];
      // Legacy fields for backwards compatibility
      hypothesis_evaluation?: string;
      key_findings?: string[];
      variant_comparison?: string | null;
      suggested_followups?: string[];
      generated_at: string;
      model_used: string;
      error?: string;
    };
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

interface StalledVariant {
  id: number;
  game_id: string;
  variant: string;
  state: 'calling_api' | 'processing';
  last_heartbeat_at: string;
  last_api_call_started_at: string | null;
  process_id: number | null;
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
  onEditInLabAssistant?: (experiment: ExperimentDetailType) => void;
  onBuildFromSuggestion?: (experiment: ExperimentDetailType, suggestion: NextStepSuggestion) => void;
  onOpenAssistant?: () => void;
}

export function ExperimentDetail({ experimentId, onBack, onEditInLabAssistant, onBuildFromSuggestion, onOpenAssistant }: ExperimentDetailProps) {
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
  const [pauseRequested, setPauseRequested] = useState(false);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [failureDetailsExpanded, setFailureDetailsExpanded] = useState(true);
  const [stalledVariants, setStalledVariants] = useState<StalledVariant[]>([]);
  const [resumingVariants, setResumingVariants] = useState<Set<number>>(new Set());


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
        setPauseRequested(expData.pause_requested || false);
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

  // Fetch stalled variants for running experiments
  const fetchStalledVariants = useCallback(async () => {
    if (!experimentId) return;
    try {
      const response = await fetch(
        `${config.API_URL}/api/experiments/${experimentId}/stalled?threshold_minutes=5`
      );
      const data = await response.json();
      if (data.success) {
        setStalledVariants(data.stalled_variants || []);
      }
    } catch (err) {
      console.error('Failed to fetch stalled variants:', err);
    }
  }, [experimentId]);

  // Resume a stalled variant
  const handleResumeVariant = async (variantId: number) => {
    setResumingVariants((prev) => new Set(prev).add(variantId));
    try {
      const response = await fetch(
        `${config.API_URL}/api/experiments/${experimentId}/variants/${variantId}/resume`,
        { method: 'POST' }
      );
      const data = await response.json();
      if (data.success) {
        // Refresh stalled variants list
        fetchStalledVariants();
        fetchExperiment();
      } else {
        setError(data.error || 'Failed to resume variant');
      }
    } catch (err) {
      console.error('Failed to resume variant:', err);
      setError('Failed to resume variant');
    } finally {
      setResumingVariants((prev) => {
        const next = new Set(prev);
        next.delete(variantId);
        return next;
      });
    }
  };

  // Initial load
  useEffect(() => {
    fetchExperiment();
  }, [fetchExperiment]);

  // Auto-refresh for running experiments
  // Note: experimentId is stable, so fetchExperiment reference is stable
  // Only re-run effect when status changes to avoid multiple intervals
  useEffect(() => {
    if (experiment?.status !== 'running') return;

    const interval = setInterval(fetchExperiment, 5000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experiment?.status, experimentId]);

  // Check for stalled variants periodically while running
  useEffect(() => {
    if (experiment?.status !== 'running') {
      setStalledVariants([]);
      return;
    }

    // Initial fetch
    fetchStalledVariants();

    // Check every 30 seconds
    const interval = setInterval(fetchStalledVariants, 30000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experiment?.status, experimentId]);


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

  const handleArchive = async () => {
    setArchiveLoading(true);
    try {
      const isArchived = experiment?.tags?.includes('_archived');
      const endpoint = isArchived ? 'unarchive' : 'archive';
      const response = await fetch(`${config.API_URL}/api/experiments/${experimentId}/${endpoint}`, {
        method: 'POST',
      });
      const data = await response.json();
      if (data.success) {
        fetchExperiment();
      } else {
        setError(data.error || `Failed to ${endpoint} experiment`);
      }
    } catch (err) {
      console.error('Failed to archive/unarchive experiment:', err);
      setError('Failed to archive experiment');
    } finally {
      setArchiveLoading(false);
    }
  };


  const formatDuration = (seconds: number) => {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
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
  const isPausing = experiment.status === 'running' && pauseRequested;
  const isArchived = experiment.tags?.includes('_archived');

  return (
    <div className="experiment-detail">
      {/* Sticky Toolbar */}
      <div className="experiment-detail__toolbar">
        <div className="experiment-detail__toolbar-left">
          <h2 className="experiment-detail__name">{experiment.name}</h2>
          <span className={`status-badge ${isPausing ? 'status-badge--pausing' : statusConfig.className}`}>
            {isPausing ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                Pausing...
              </>
            ) : (
              <>
                {statusConfig.icon}
                {statusConfig.label}
              </>
            )}
          </span>
          {isArchived && (
            <span className="experiment-detail__archived-badge">
              <Archive size={12} />
              Archived
            </span>
          )}
        </div>
        <div className="experiment-detail__toolbar-actions">
          <button
            className="experiment-detail__refresh-btn"
            onClick={fetchExperiment}
            type="button"
            title="Refresh"
          >
            <RefreshCw size={16} />
          </button>
          {onOpenAssistant && (
            <button
              className="experiment-detail__chat-btn"
              onClick={onOpenAssistant}
              type="button"
              title="Chat with Assistant"
            >
              <MessageSquare size={16} />
              Ask Assistant
            </button>
          )}
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
                disabled={pauseLoading || isPausing}
                title="Pause Experiment"
              >
                {pauseLoading || isPausing ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Pause size={16} />
                )}
                {pauseLoading || isPausing ? 'Pausing...' : 'Pause'}
              </button>
            </>
          )}
          {(experiment.status === 'paused' || experiment.status === 'interrupted') && (
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
          {/* Archive/Unarchive button - show for non-running experiments */}
          {experiment.status !== 'running' && (
            <button
              className={`experiment-detail__archive-btn ${isArchived ? 'experiment-detail__archive-btn--unarchive' : ''}`}
              onClick={handleArchive}
              type="button"
              disabled={archiveLoading}
              title={isArchived ? 'Unarchive Experiment' : 'Archive Experiment'}
            >
              {archiveLoading ? (
                <Loader2 size={16} className="animate-spin" />
              ) : isArchived ? (
                <ArchiveRestore size={16} />
              ) : (
                <Archive size={16} />
              )}
              {isArchived ? 'Unarchive' : 'Archive'}
            </button>
          )}
        </div>
      </div>

      {/* Scrollable Content */}
      <div className="experiment-detail__content">
        {/* Error Banner for failed/interrupted experiments */}
        {(experiment.status === 'failed' || experiment.status === 'interrupted') && experiment.notes && (
          <div className={`experiment-detail__error-banner experiment-detail__error-banner--${experiment.status}`}>
            <AlertTriangle size={18} />
            <div className="experiment-detail__error-banner-content">
              <span className="experiment-detail__error-banner-title">
                {experiment.status === 'failed' ? 'Experiment Failed' : 'Experiment Interrupted'}
              </span>
              <span className="experiment-detail__error-banner-message">{experiment.notes}</span>
            </div>
            <div className="experiment-detail__error-banner-actions">
              {experiment.status === 'failed' && onEditInLabAssistant && (
                <button
                  className="experiment-detail__error-banner-action experiment-detail__error-banner-action--edit"
                  onClick={() => onEditInLabAssistant(experiment)}
                  type="button"
                >
                  <Wand2 size={14} />
                  Edit in Lab Assistant
                </button>
              )}
              {experiment.status === 'interrupted' && (
                <button
                  className="experiment-detail__error-banner-action"
                  onClick={handleResume}
                  type="button"
                  disabled={resumeLoading}
                >
                  {resumeLoading ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                  Resume
                </button>
              )}
            </div>
          </div>
        )}

        {/* Failure Details Section for failed experiments */}
        {experiment.status === 'failed' && experiment.summary?.failed_tournaments && experiment.summary.failed_tournaments.length > 0 && (
          <div className="experiment-detail__section experiment-detail__section--failure">
            <button
              className="experiment-detail__section-toggle"
              onClick={() => setFailureDetailsExpanded(!failureDetailsExpanded)}
              type="button"
            >
              {failureDetailsExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              <h3 className="experiment-detail__section-title">
                <AlertTriangle size={16} />
                Failure Details ({experiment.summary.failed_tournaments.length} tournament{experiment.summary.failed_tournaments.length !== 1 ? 's' : ''})
              </h3>
            </button>
            {failureDetailsExpanded && (
              <div className="experiment-detail__failure-list">
                {experiment.summary.failed_tournaments.map((failure, idx) => (
                  <div key={idx} className="experiment-detail__failure-item">
                    <div className="experiment-detail__failure-header">
                      <span className="experiment-detail__failure-tournament">
                        Tournament #{failure.tournament_number}
                      </span>
                      {failure.variant && (
                        <span className="experiment-detail__failure-variant">
                          {failure.variant}
                        </span>
                      )}
                      <span className="experiment-detail__failure-type">
                        {failure.error_type}
                      </span>
                      {failure.duration_seconds > 0 && (
                        <span className="experiment-detail__failure-duration">
                          {formatDuration(failure.duration_seconds)}
                        </span>
                      )}
                    </div>
                    <div className="experiment-detail__failure-message">
                      {failure.error}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Header Info */}
        <div className="experiment-detail__header">
          {experiment.description && (
            <p className="experiment-detail__description">{experiment.description}</p>
          )}
          {experiment.hypothesis && (
            <p className="experiment-detail__hypothesis">
              <strong>Hypothesis:</strong> {experiment.hypothesis}
            </p>
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

      {/* AI Interpretation */}
      {/* Show error state if AI interpretation failed */}
      {experiment.status === 'completed' && summary?.ai_interpretation?.error && (
        <div className="experiment-detail__section experiment-detail__section--ai experiment-detail__section--error">
          <h3 className="experiment-detail__section-title">
            <Brain size={18} />
            AI Analysis
          </h3>
          <div className="experiment-detail__ai-placeholder experiment-detail__ai-placeholder--error">
            <AlertTriangle size={20} />
            <p>Failed to generate AI analysis: {summary.ai_interpretation.error}</p>
          </div>
        </div>
      )}

      {/* Show loading state if completed but no summary or no AI interpretation yet */}
      {experiment.status === 'completed' && (!summary || !summary.ai_interpretation) && (
        <div className="experiment-detail__section experiment-detail__section--ai experiment-detail__section--placeholder">
          <h3 className="experiment-detail__section-title">
            <Brain size={18} />
            AI Analysis
          </h3>
          <div className="experiment-detail__ai-placeholder">
            <Loader2 size={20} className="animate-spin" />
            <p>Generating AI analysis...</p>
          </div>
        </div>
      )}

      {/* Show actual AI interpretation when available */}
      {summary?.ai_interpretation && !summary.ai_interpretation.error && (
        <div className="experiment-detail__section experiment-detail__section--ai">
          <h3 className="experiment-detail__section-title">
            <Brain size={18} />
            AI Analysis
            <span className="experiment-detail__ai-meta">
              {summary.ai_interpretation.model_used} • {new Date(summary.ai_interpretation.generated_at).toLocaleDateString()}
            </span>
          </h3>

          <div className="experiment-detail__ai-content">
            {/* Summary */}
            <div className="experiment-detail__ai-summary">
              <p>{summary.ai_interpretation.summary}</p>
            </div>

            {/* Verdict (new) or Hypothesis Evaluation (legacy) */}
            {(summary.ai_interpretation.verdict || summary.ai_interpretation.hypothesis_evaluation) && (
              <div className="experiment-detail__ai-block">
                <h4>
                  <FlaskRound size={14} />
                  Verdict
                </h4>
                <p>{summary.ai_interpretation.verdict || summary.ai_interpretation.hypothesis_evaluation}</p>
              </div>
            )}

            {/* Surprises (new) or Key Findings (legacy) - only show if non-empty */}
            {((summary.ai_interpretation.surprises?.length ?? 0) > 0 || (summary.ai_interpretation.key_findings?.length ?? 0) > 0) && (
              <div className="experiment-detail__ai-block">
                <h4>
                  <Lightbulb size={14} />
                  {summary.ai_interpretation.surprises ? 'Surprises' : 'Key Findings'}
                </h4>
                <ul className="experiment-detail__ai-list">
                  {(summary.ai_interpretation.surprises || summary.ai_interpretation.key_findings || []).map((item, idx) => (
                    <li key={idx}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Next Steps (new) or Suggested Follow-ups (legacy) */}
            {((summary.ai_interpretation.next_steps?.length ?? 0) > 0 || (summary.ai_interpretation.suggested_followups?.length ?? 0) > 0) && (
              <div className="experiment-detail__ai-block">
                <h4>
                  <Target size={14} />
                  Next Steps
                </h4>
                <div className="experiment-detail__suggestions-grid">
                  {(summary.ai_interpretation.next_steps || summary.ai_interpretation.suggested_followups || []).map((item, idx) => {
                    // Handle both structured suggestions and legacy string format
                    const isStructured = typeof item === 'object' && item !== null && 'hypothesis' in item;
                    const suggestion = isStructured ? item as NextStepSuggestion : null;

                    if (suggestion && onBuildFromSuggestion) {
                      return (
                        <div key={idx} className="experiment-detail__suggestion-card">
                          <div className="experiment-detail__suggestion-content">
                            <p className="experiment-detail__suggestion-hypothesis">{suggestion.hypothesis}</p>
                            <p className="experiment-detail__suggestion-description">{suggestion.description}</p>
                          </div>
                          <button
                            className="experiment-detail__suggestion-action"
                            onClick={() => onBuildFromSuggestion(experiment, suggestion)}
                            type="button"
                          >
                            Build Experiment &rarr;
                          </button>
                        </div>
                      );
                    }

                    // Legacy string format - render as simple list item
                    return (
                      <div key={idx} className="experiment-detail__suggestion-card experiment-detail__suggestion-card--simple">
                        <p className="experiment-detail__suggestion-text">{typeof item === 'string' ? item : JSON.stringify(item)}</p>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
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
              // Check if this variant is stalled
              const stalledVariant = stalledVariants.find((sv) => sv.variant === label);
              const isStalled = !!stalledVariant;
              const isResuming = stalledVariant ? resumingVariants.has(stalledVariant.id) : false;
              return (
                <div key={label} className={`experiment-detail__variant-card${isStalled ? ' experiment-detail__variant-card--stalled' : ''}`}>
                  <div className="experiment-detail__variant-header">
                    <h4 className="experiment-detail__variant-label">{label}</h4>
                    <div className="experiment-detail__variant-header-right">
                      {isStalled && (
                        <>
                          <span className="experiment-detail__stalled-badge" title={`State: ${stalledVariant.state}, Last activity: ${stalledVariant.last_heartbeat_at}`}>
                            <AlertTriangle size={12} /> Stalled
                          </span>
                          <button
                            className="experiment-detail__resume-variant-btn"
                            onClick={() => handleResumeVariant(stalledVariant.id)}
                            disabled={isResuming}
                            title="Resume this stalled variant"
                          >
                            {isResuming ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                            Resume
                          </button>
                        </>
                      )}
                      {variantSummary?.model_config && (
                        <span className="experiment-detail__variant-model">
                          {variantSummary.model_config.provider}/{variantSummary.model_config.model}
                        </span>
                      )}
                    </div>
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

                  {/* Quality Indicators Section */}
                  {variantLive.quality_indicators && (
                    <div className="experiment-detail__variant-section">
                      <span className="experiment-detail__variant-section-label">Quality Indicators</span>
                      <div className="experiment-detail__decision-row">
                        <span className={`experiment-detail__decision-metric ${variantLive.quality_indicators.suspicious_allins > 0 ? 'experiment-detail__decision-metric--mistake' : ''}`}>
                          {variantLive.quality_indicators.suspicious_allins} Suspicious All-ins
                        </span>
                        <span className="experiment-detail__decision-metric">
                          {variantLive.quality_indicators.marginal_allins} Marginal
                        </span>
                        <span className={`experiment-detail__decision-metric ${variantLive.quality_indicators.fold_mistake_rate > 50 ? 'experiment-detail__decision-metric--mistake' : ''}`}>
                          {variantLive.quality_indicators.fold_mistakes} Fold Mistakes
                        </span>
                      </div>
                      {/* Survival Metrics */}
                      {((variantLive.quality_indicators.total_eliminations ?? 0) > 0 ||
                        (variantLive.quality_indicators.all_in_wins ?? 0) > 0 ||
                        (variantLive.quality_indicators.all_in_losses ?? 0) > 0) && (
                        <div className="experiment-detail__decision-row" style={{ marginTop: '4px' }}>
                          <span className="experiment-detail__decision-metric">
                            {variantLive.quality_indicators.total_eliminations ?? 0} Eliminations
                          </span>
                          <span className={`experiment-detail__decision-metric ${variantLive.quality_indicators.all_in_survival_rate != null && variantLive.quality_indicators.all_in_survival_rate < 40 ? 'experiment-detail__decision-metric--mistake' : ''}`}>
                            All-in: {variantLive.quality_indicators.all_in_wins ?? 0}W/{variantLive.quality_indicators.all_in_losses ?? 0}L
                            {variantLive.quality_indicators.all_in_survival_rate != null && ` (${variantLive.quality_indicators.all_in_survival_rate}%)`}
                          </span>
                        </div>
                      )}
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

                  {/* Cost Metrics Section */}
                  {variantLive.cost_metrics && variantLive.cost_metrics.total_cost > 0 && (
                    <div className="experiment-detail__variant-section">
                      <span className="experiment-detail__variant-section-label">
                        <DollarSign size={12} />
                        Cost Analytics
                      </span>
                      <div className="experiment-detail__cost-summary">
                        <span className="experiment-detail__cost-total">
                          {formatCost(variantLive.cost_metrics.total_cost)}
                        </span>
                        <span className="experiment-detail__cost-label">total</span>
                      </div>
                      <div className="experiment-detail__cost-grid">
                        <div className="experiment-detail__cost-cell">
                          <span className="experiment-detail__cost-metric-label">Per Hand</span>
                          <span className="experiment-detail__cost-metric-value">
                            {formatCost(variantLive.cost_metrics.cost_per_hand)}
                          </span>
                        </div>
                        <div className="experiment-detail__cost-cell">
                          <span className="experiment-detail__cost-metric-label">Per Decision</span>
                          <span className="experiment-detail__cost-metric-value">
                            {formatCost(variantLive.cost_metrics.avg_cost_per_decision)}
                          </span>
                        </div>
                      </div>
                      {Object.keys(variantLive.cost_metrics.by_model).length > 0 && (
                        <div className="experiment-detail__cost-by-model">
                          {Object.entries(variantLive.cost_metrics.by_model).map(([model, data]) => (
                            <div key={model} className="experiment-detail__cost-model-row">
                              <span className="experiment-detail__cost-model-name">{model}</span>
                              <span className="experiment-detail__cost-model-value">
                                {formatCost(data.cost)} ({data.calls.toLocaleString()} calls)
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
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
      </div>{/* End of experiment-detail__content */}

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
