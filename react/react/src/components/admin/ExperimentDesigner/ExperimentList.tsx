import { useState, useEffect, useCallback } from 'react';
import { RefreshCw, Clock, CheckCircle, XCircle, Loader2, Pause } from 'lucide-react';
import type { ExperimentSummary } from './types';
import { config } from '../../../config';

type ExperimentStatus = 'pending' | 'running' | 'completed' | 'failed' | 'paused';

interface ExperimentListProps {
  onViewExperiment: (experiment: ExperimentSummary) => void;
  onNewExperiment: () => void;
}

const STATUS_CONFIG: Record<ExperimentStatus, { icon: React.ReactNode; className: string; label: string }> = {
  pending: {
    icon: <Clock size={14} />,
    className: 'status-badge--pending',
    label: 'Pending',
  },
  running: {
    icon: <Loader2 size={14} className="animate-spin" />,
    className: 'status-badge--running',
    label: 'Running',
  },
  completed: {
    icon: <CheckCircle size={14} />,
    className: 'status-badge--completed',
    label: 'Completed',
  },
  failed: {
    icon: <XCircle size={14} />,
    className: 'status-badge--failed',
    label: 'Failed',
  },
  paused: {
    icon: <Pause size={14} />,
    className: 'status-badge--paused',
    label: 'Paused',
  },
};

export function ExperimentList({ onViewExperiment, onNewExperiment }: ExperimentListProps) {
  const [experiments, setExperiments] = useState<ExperimentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<ExperimentStatus | 'all'>('all');

  const fetchExperiments = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (statusFilter !== 'all') {
        params.append('status', statusFilter);
      }

      const response = await fetch(`${config.API_URL}/api/experiments?${params}`);
      const data = await response.json();

      if (data.success) {
        setExperiments(data.experiments);
        setError(null);
      } else {
        setError(data.error || 'Failed to load experiments');
      }
    } catch (err) {
      console.error('Failed to fetch experiments:', err);
      setError('Failed to connect to server');
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

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

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

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
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as ExperimentStatus | 'all')}
        >
          <option value="all">All Status</option>
          <option value="pending">Pending</option>
          <option value="running">Running</option>
          <option value="paused">Paused</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </select>

        <button
          className="experiment-list__refresh-btn"
          onClick={fetchExperiments}
          type="button"
          title="Refresh"
        >
          <RefreshCw size={16} />
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
            onClick={onNewExperiment}
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
                return (
                  <tr
                    key={experiment.id}
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
                            {getProgress(experiment)}
                          </span>
                        </div>
                      ) : (
                        getProgress(experiment)
                      )}
                    </td>
                    <td className="experiment-list__model">
                      {experiment.provider && experiment.model ? (
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
