import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { RefreshCw, Loader2, Archive, Plus, Beaker, Repeat2 } from 'lucide-react';
import type { ExperimentSummary, ExperimentType } from './types';
import { config } from '../../../config';
import { formatDate } from '../../../utils/formatters';
import { logger } from '../../../utils/logger';
import { STATUS_CONFIG_SMALL as STATUS_CONFIG, type ExperimentStatus } from './experimentStatus';
import { useViewport } from '../../../hooks/useViewport';
import { MobileExperimentList } from './MobileExperimentList';

interface ExperimentListProps {
  onViewExperiment: (experiment: ExperimentSummary) => void;
}

export function ExperimentList({ onViewExperiment }: ExperimentListProps) {
  const navigate = useNavigate();
  const { isMobile } = useViewport();
  const [experiments, setExperiments] = useState<ExperimentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<ExperimentStatus | 'all'>('all');
  const [typeFilter, setTypeFilter] = useState<ExperimentType | 'all'>('all');
  const [includeArchived, setIncludeArchived] = useState(false);

  const fetchExperiments = useCallback(async () => {
    try {
      // Build params for tournaments
      const tournamentParams = new URLSearchParams();
      if (statusFilter !== 'all') {
        tournamentParams.append('status', statusFilter);
      }
      if (includeArchived) {
        tournamentParams.append('include_archived', 'true');
      }

      // Fetch both tournament and replay experiments in parallel
      const [tournamentResponse, replayResponse] = await Promise.all([
        typeFilter !== 'replay' ? fetch(`${config.API_URL}/api/experiments?${tournamentParams}`) : null,
        typeFilter !== 'tournament' ? fetch(`${config.API_URL}/api/replay-experiments?${tournamentParams}`) : null,
      ]);

      const allExperiments: ExperimentSummary[] = [];

      // Process tournament experiments
      if (tournamentResponse) {
        const tournamentData = await tournamentResponse.json();
        if (tournamentData.success && tournamentData.experiments) {
          const tournaments = tournamentData.experiments.map((exp: ExperimentSummary) => ({
            ...exp,
            experiment_type: 'tournament' as ExperimentType,
          }));
          allExperiments.push(...tournaments);
        }
      }

      // Process replay experiments
      if (replayResponse) {
        const replayData = await replayResponse.json();
        if (replayData.success && replayData.experiments) {
          // Map replay experiment fields to our ExperimentSummary format
          const replays = replayData.experiments.map((exp: {
            id: number;
            name: string;
            description?: string;
            hypothesis?: string;
            status: string;
            created_at: string;
            completed_at?: string;
            capture_count?: number;
            variant_count?: number;
          }) => ({
            id: exp.id,
            name: exp.name,
            description: exp.description || '',
            hypothesis: exp.hypothesis || '',
            tags: [],
            status: exp.status as ExperimentStatus,
            created_at: exp.created_at,
            completed_at: exp.completed_at || null,
            games_count: exp.capture_count || 0,
            num_tournaments: exp.variant_count || 0,
            model: null,
            provider: null,
            summary: null,
            experiment_type: 'replay' as ExperimentType,
          }));
          allExperiments.push(...replays);
        }
      }

      // Sort by created_at descending
      allExperiments.sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );

      setExperiments(allExperiments);
      setError(null);
    } catch (err) {
      logger.error('Failed to fetch experiments:', err);
      setError('Failed to connect to server');
    } finally {
      setLoading(false);
    }
  }, [statusFilter, typeFilter, includeArchived]);

  // Initial load
  useEffect(() => {
    fetchExperiments();
  }, [fetchExperiments]);

  // Auto-refresh for running experiments
  useEffect(() => {
    const hasRunning = experiments.some(e => e.status === 'running');
    if (!hasRunning) return;

    const interval = setInterval(fetchExperiments, 5000);
    return () => clearInterval(interval);
  }, [experiments, fetchExperiments]);

  const getProgress = (experiment: ExperimentSummary) => {
    if (experiment.status === 'completed' || experiment.status === 'failed') {
      return `${experiment.games_count}/${experiment.num_tournaments}`;
    }
    if (experiment.status === 'running') {
      return `${experiment.games_count}/${experiment.num_tournaments}`;
    }
    return `0/${experiment.num_tournaments}`;
  };

  const getProgressPct = (experiment: ExperimentSummary) => {
    const total = experiment.num_tournaments || 1;
    const current = experiment.games_count || 0;
    return Math.min(100, Math.round((current / total) * 100));
  };

  // Handler for creating new experiment - navigates to dedicated route
  const handleNewExperiment = useCallback(() => {
    navigate('/admin/experiments/new');
  }, [navigate]);

  // Mobile view - render MobileExperimentList
  if (isMobile) {
    return (
      <MobileExperimentList
        experiments={experiments}
        loading={loading}
        error={error}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        includeArchived={includeArchived}
        onIncludeArchivedChange={setIncludeArchived}
        onRefresh={fetchExperiments}
        onViewExperiment={onViewExperiment}
        onNewExperiment={handleNewExperiment}
      />
    );
  }

  // Desktop view - original table-based layout
  if (loading) {
    return (
      <div className="experiment-list__loading">
        <Loader2 size={24} className="animate-spin" />
        <span>Loading experiments...</span>
      </div>
    );
  }

  return (
    <div className="experiment-list">
      {/* Filter Bar */}
      <div className="experiment-list__filters">
        <select
          className="experiment-list__filter-select"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value as ExperimentType | 'all')}
        >
          <option value="all">All Types</option>
          <option value="tournament">Tournaments</option>
          <option value="replay">Replays</option>
        </select>

        <select
          className="experiment-list__filter-select"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as ExperimentStatus | 'all')}
        >
          <option value="all">All Status</option>
          <option value="pending">Pending</option>
          <option value="running">Running</option>
          <option value="paused">Paused</option>
          <option value="interrupted">Interrupted</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </select>

        <label className="experiment-list__archive-toggle">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(e) => setIncludeArchived(e.target.checked)}
          />
          <Archive size={14} />
          <span>Show archived</span>
        </label>

        <button
          className="experiment-list__refresh-btn"
          onClick={fetchExperiments}
          type="button"
          title="Refresh"
        >
          <RefreshCw size={16} />
        </button>

        <button
          className="experiment-list__new-btn"
          onClick={handleNewExperiment}
          type="button"
        >
          <Plus size={16} />
          New Experiment
        </button>
      </div>

      {error && (
        <div className="experiment-list__error">
          {error}
        </div>
      )}

      {experiments.length === 0 ? (
        <div className="experiment-list__empty">
          <p>No experiments found.</p>
          <button
            className="experiment-list__empty-btn"
            onClick={handleNewExperiment}
            type="button"
          >
            Create your first experiment
          </button>
        </div>
      ) : (
        <div className="experiment-list__table-wrapper">
          <table className="experiment-list__table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Model</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {experiments.map((experiment) => {
                const statusConfig = STATUS_CONFIG[experiment.status];
                const isReplay = experiment.experiment_type === 'replay';
                return (
                  <tr
                    key={`${experiment.experiment_type}-${experiment.id}`}
                    className="experiment-list__row"
                    onClick={() => onViewExperiment(experiment)}
                  >
                    <td className="experiment-list__name-cell">
                      <span className="experiment-list__name">{experiment.name}</span>
                      {experiment.description && (
                        <span className="experiment-list__description">{experiment.description}</span>
                      )}
                    </td>
                    <td>
                      <span className={`experiment-list__type-badge experiment-list__type-badge--${experiment.experiment_type || 'tournament'}`}>
                        {isReplay ? <Repeat2 size={12} /> : <Beaker size={12} />}
                        {isReplay ? 'Replay' : 'Tournament'}
                      </span>
                    </td>
                    <td>
                      <span className={`status-badge ${statusConfig.className}`}>
                        {statusConfig.icon}
                        {statusConfig.label}
                      </span>
                    </td>
                    <td className="experiment-list__progress">
                      {experiment.status === 'running' ? (
                        <div className="experiment-list__progress-wrapper">
                          <div className="experiment-list__progress-bar-container">
                            <div
                              className="experiment-list__progress-bar"
                              style={{ width: `${getProgressPct(experiment)}%` }}
                            />
                          </div>
                          <span className="experiment-list__progress-text">
                            {isReplay ? `${experiment.games_count} captures` : getProgress(experiment)}
                          </span>
                        </div>
                      ) : (
                        isReplay
                          ? `${experiment.games_count} captures x ${experiment.num_tournaments} variants`
                          : getProgress(experiment)
                      )}
                    </td>
                    <td className="experiment-list__model">
                      {isReplay ? (
                        <span className="experiment-list__model--default">varied</span>
                      ) : experiment.provider && experiment.model ? (
                        <span>{experiment.provider}/{experiment.model}</span>
                      ) : (
                        <span className="experiment-list__model--default">default</span>
                      )}
                    </td>
                    <td className="experiment-list__date">
                      {formatDate(experiment.created_at)}
                    </td>
                    <td>
                      <button
                        className="experiment-list__view-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          onViewExperiment(experiment);
                        }}
                        type="button"
                      >
                        View
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default ExperimentList;
