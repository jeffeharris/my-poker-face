import { Clock, CheckCircle, XCircle, Loader2, Pause, AlertTriangle, ChevronRight } from 'lucide-react';
import type { ExperimentSummary } from './types';
import type { ExperimentStatus } from './experimentStatus';
import { formatDate } from '../../../utils/formatters';
import './ExperimentCard.css';

interface ExperimentCardProps {
  experiment: ExperimentSummary;
  onClick: () => void;
}

// Status icon mapping
const STATUS_ICONS: Record<ExperimentStatus, React.ReactNode> = {
  pending: <Clock size={14} />,
  running: <Loader2 size={14} className="experiment-card__spin" />,
  completed: <CheckCircle size={14} />,
  failed: <XCircle size={14} />,
  paused: <Pause size={14} />,
  interrupted: <AlertTriangle size={14} />,
};

// Status labels
const STATUS_LABELS: Record<ExperimentStatus, string> = {
  pending: 'Pending',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  paused: 'Paused',
  interrupted: 'Interrupted',
};

/**
 * ExperimentCard - Mobile-optimized card for experiment list
 *
 * Displays:
 * - Name and description
 * - Status badge with icon
 * - Progress bar (for running experiments)
 * - Model info
 * - Created date
 */
export function ExperimentCard({ experiment, onClick }: ExperimentCardProps) {
  const status = experiment.status as ExperimentStatus;
  const progressPct = experiment.num_tournaments > 0
    ? Math.min(100, Math.round((experiment.games_count / experiment.num_tournaments) * 100))
    : 0;

  const modelDisplay = experiment.provider && experiment.model
    ? `${experiment.provider}/${experiment.model}`
    : 'default';

  return (
    <button
      className="experiment-card"
      onClick={onClick}
      type="button"
    >
      {/* Header: Name + Status */}
      <div className="experiment-card__header">
        <h3 className="experiment-card__name">{experiment.name}</h3>
        <span className={`experiment-card__status experiment-card__status--${status}`}>
          {STATUS_ICONS[status]}
          <span>{STATUS_LABELS[status]}</span>
        </span>
      </div>

      {/* Description */}
      {experiment.description && (
        <p className="experiment-card__description">{experiment.description}</p>
      )}

      {/* Progress (for running experiments) */}
      {status === 'running' && (
        <div className="experiment-card__progress">
          <div className="experiment-card__progress-bar-container">
            <div
              className="experiment-card__progress-bar"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <span className="experiment-card__progress-text">
            {experiment.games_count}/{experiment.num_tournaments} tournaments
          </span>
        </div>
      )}

      {/* Completed progress */}
      {(status === 'completed' || status === 'failed' || status === 'paused') && (
        <div className="experiment-card__stats">
          <span className="experiment-card__stat">
            {experiment.games_count}/{experiment.num_tournaments} tournaments
          </span>
        </div>
      )}

      {/* Footer: Meta info */}
      <div className="experiment-card__footer">
        <div className="experiment-card__meta">
          <span className="experiment-card__model">{modelDisplay}</span>
          <span className="experiment-card__divider">•</span>
          <span className="experiment-card__date">{formatDate(experiment.created_at)}</span>
        </div>
        <ChevronRight size={18} className="experiment-card__chevron" />
      </div>
    </button>
  );
}

export default ExperimentCard;
