import type { PromptCapture, CaptureFilters } from './types';
import {
  formatCardsCanonical,
  formatLabelName,
  formatPotOdds,
  getActionColor,
  getLabelSeverity,
  isSuspiciousFold,
} from './utils';

interface CaptureListProps {
  captures: PromptCapture[];
  total: number;
  loading: boolean;
  selectedCapture: PromptCapture | null;
  filters: CaptureFilters;
  onFiltersChange: (filters: CaptureFilters) => void;
  onSelectCapture: (captureId: number) => void;
}

// The left-hand capture list (also used full-width as the mobile list view).
export function CaptureList({
  captures,
  total,
  loading,
  selectedCapture,
  filters,
  onFiltersChange,
  onSelectCapture,
}: CaptureListProps) {
  return (
    <div className="capture-list">
      <h3>Captures ({total})</h3>
      {loading && <div className="loading">Loading...</div>}

      {captures.map((capture) => (
        <div
          key={capture.id}
          className={`capture-item ${selectedCapture?.id === capture.id ? 'selected' : ''} ${isSuspiciousFold(capture) ? 'suspicious' : ''}`}
          onClick={() => onSelectCapture(capture.id)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onSelectCapture(capture.id);
            }
          }}
        >
          <div className="capture-header">
            <span className="capture-player">{capture.player_name}</span>
            <span className={`capture-action ${getActionColor(capture.action_taken)}`}>
              {capture.action_taken?.toUpperCase()}
            </span>
          </div>
          <div className="capture-details">
            <span className="capture-phase">{capture.phase}</span>
            <span className="capture-pot">Pot: ${capture.pot_total}</span>
            <span className="capture-odds">{formatPotOdds(capture.pot_odds)} odds</span>
          </div>
          {capture.player_hand && capture.player_hand.length > 0 && (
            <div className="capture-hand">{formatCardsCanonical(capture.player_hand)}</div>
          )}
          {/* Display error info */}
          {capture.error_type && (
            <div className="capture-error" title={capture.error_description || undefined}>
              <span className="error-badge">{capture.error_type.replace(/_/g, ' ')}</span>
              {(capture.correction_attempt ?? 0) > 0 && (
                <span className="correction-badge">Attempt #{capture.correction_attempt}</span>
              )}
              {capture.error_description && (
                <span className="error-description">{capture.error_description}</span>
              )}
            </div>
          )}
          {/* Display labels for this capture */}
          {capture.labels && capture.labels.length > 0 && (
            <div className="capture-labels">
              {capture.labels.map(({ label }) => (
                <span
                  key={label}
                  className={`capture-label capture-label--${getLabelSeverity(label)}`}
                >
                  {formatLabelName(label)}
                </span>
              ))}
            </div>
          )}
          {isSuspiciousFold(capture) &&
            !capture.labels?.some((l) => l.label === 'suspicious_fold') && (
              <div className="suspicious-badge">Suspicious Fold</div>
            )}
        </div>
      ))}

      {/* Pagination */}
      {total > filters.limit! && (
        <div className="pagination">
          <button
            disabled={filters.offset === 0}
            onClick={() =>
              onFiltersChange({
                ...filters,
                offset: Math.max(0, (filters.offset || 0) - filters.limit!),
              })
            }
          >
            Previous
          </button>
          <span>
            {Math.floor((filters.offset || 0) / filters.limit!) + 1} /{' '}
            {Math.ceil(total / filters.limit!)}
          </span>
          <button
            disabled={(filters.offset || 0) + filters.limit! >= total}
            onClick={() =>
              onFiltersChange({ ...filters, offset: (filters.offset || 0) + filters.limit! })
            }
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
