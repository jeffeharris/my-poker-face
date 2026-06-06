import { RefreshCw } from 'lucide-react';
import type { CaptureFilters, LabelStats } from './types';
import { DEFAULT_FILTERS, formatEmotionName, formatLabelName, getLabelSeverity } from './utils';

interface DesktopFilterBarProps {
  filters: CaptureFilters;
  onFiltersChange: (filters: CaptureFilters) => void;
  availableEmotions: string[];
  labelStats: LabelStats | null;
  loading: boolean;
  onRefresh: () => void;
  onToggleLabel: (label: string) => void;
}

// The inline desktop filter row (mobile uses MobileFilterBar + the sheet).
export function DesktopFilterBar({
  filters,
  onFiltersChange,
  availableEmotions,
  labelStats,
  loading,
  onRefresh,
  onToggleLabel,
}: DesktopFilterBarProps) {
  return (
    <div className="debugger-filters">
      <select
        value={filters.action || ''}
        onChange={(e) =>
          onFiltersChange({ ...filters, action: e.target.value || undefined, offset: 0 })
        }
      >
        <option value="">All Actions</option>
        <option value="fold">Fold</option>
        <option value="check">Check</option>
        <option value="call">Call</option>
        <option value="raise">Raise</option>
      </select>

      <select
        value={filters.phase || ''}
        onChange={(e) =>
          onFiltersChange({ ...filters, phase: e.target.value || undefined, offset: 0 })
        }
      >
        <option value="">All Phases</option>
        <option value="PRE_FLOP">Pre-Flop</option>
        <option value="FLOP">Flop</option>
        <option value="TURN">Turn</option>
        <option value="RIVER">River</option>
      </select>

      <input
        type="number"
        placeholder="Min Pot Odds"
        value={filters.min_pot_odds || ''}
        onChange={(e) =>
          onFiltersChange({
            ...filters,
            min_pot_odds: e.target.value ? parseFloat(e.target.value) : undefined,
            offset: 0,
          })
        }
      />

      <select
        value={filters.error_type || ''}
        onChange={(e) =>
          onFiltersChange({ ...filters, error_type: e.target.value || undefined, offset: 0 })
        }
      >
        <option value="">All Error Types</option>
        <option value="malformed_json">Malformed JSON</option>
        <option value="missing_field">Missing Field</option>
        <option value="invalid_action">Invalid Action</option>
        <option value="semantic_error">Semantic Error</option>
      </select>

      <select
        value={filters.has_error === undefined ? '' : filters.has_error.toString()}
        onChange={(e) =>
          onFiltersChange({
            ...filters,
            has_error: e.target.value === '' ? undefined : e.target.value === 'true',
            offset: 0,
          })
        }
      >
        <option value="">All (Errors)</option>
        <option value="true">Has Error</option>
        <option value="false">No Error</option>
      </select>

      <select
        value={filters.is_correction === undefined ? '' : filters.is_correction.toString()}
        onChange={(e) =>
          onFiltersChange({
            ...filters,
            is_correction: e.target.value === '' ? undefined : e.target.value === 'true',
            offset: 0,
          })
        }
      >
        <option value="">All (Corrections)</option>
        <option value="false">Original Only</option>
        <option value="true">Corrections Only</option>
      </select>

      <select
        value={filters.display_emotion || ''}
        onChange={(e) =>
          onFiltersChange({
            ...filters,
            display_emotion: e.target.value || undefined,
            offset: 0,
          })
        }
      >
        <option value="">All (Emotion)</option>
        {availableEmotions.map((e) => (
          <option key={e} value={e}>
            {formatEmotionName(e)}
          </option>
        ))}
      </select>

      <input
        type="number"
        placeholder="Min Tilt"
        value={filters.min_tilt_level ?? ''}
        onChange={(e) =>
          onFiltersChange({
            ...filters,
            min_tilt_level: e.target.value ? parseFloat(e.target.value) : undefined,
            offset: 0,
          })
        }
        min={0}
        max={1}
        step={0.1}
        style={{ width: '90px' }}
      />

      <input
        type="number"
        placeholder="Max Tilt"
        value={filters.max_tilt_level ?? ''}
        onChange={(e) =>
          onFiltersChange({
            ...filters,
            max_tilt_level: e.target.value ? parseFloat(e.target.value) : undefined,
            offset: 0,
          })
        }
        min={0}
        max={1}
        step={0.1}
        style={{ width: '90px' }}
      />

      {/* Label filter chips - desktop */}
      {labelStats && Object.keys(labelStats).length > 0 && (
        <div className="debugger-filter-chips debugger-filter-chips--inline">
          {Object.entries(labelStats)
            .filter(([, count]) => count > 0)
            .map(([label, count]) => (
              <button
                key={label}
                className={`label-chip label-chip--small label-chip--${getLabelSeverity(label)} ${filters.labels?.includes(label) ? 'label-chip--selected' : ''}`}
                onClick={() => onToggleLabel(label)}
                type="button"
              >
                <span className="label-chip__count">{count}</span>
                <span className="label-chip__name">{formatLabelName(label)}</span>
              </button>
            ))}
        </div>
      )}

      <button onClick={() => onFiltersChange(DEFAULT_FILTERS)}>Clear Filters</button>

      <button
        className="debugger-refresh-btn debugger-refresh-btn--desktop"
        onClick={onRefresh}
        disabled={loading}
        type="button"
        aria-label="Refresh"
      >
        <RefreshCw size={16} className={loading ? 'spinning' : ''} />
      </button>
    </div>
  );
}
