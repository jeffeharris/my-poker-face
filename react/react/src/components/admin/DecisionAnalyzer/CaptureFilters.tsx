import { RefreshCw } from 'lucide-react';
import { MobileFilterBar } from '../shared/MobileFilterBar';
import { MobileFilterSheet } from '../shared/MobileFilterSheet';
import { FilterSheetContent } from '../shared/FilterSheetContent';
import { FilterGroup } from '../shared/FilterGroup';
import type { CaptureFilters as CaptureFiltersType, LabelStats } from './types';
import { DEFAULT_FILTERS, formatEmotionName, formatLabelName, getLabelSeverity } from './utils';

interface CaptureFiltersProps {
  isMobile: boolean;
  // The mobile filter bar is hidden while viewing a capture's detail.
  showMobileDetail: boolean;
  filters: CaptureFiltersType;
  onFiltersChange: (filters: CaptureFiltersType) => void;
  availablePlayers: string[];
  availableEmotions: string[];
  labelStats: LabelStats | null;
  loading: boolean;
  onRefresh: () => void;
  onToggleLabel: (label: string) => void;
  activeFilterCount: number;
  filterSheetOpen: boolean;
  onFilterSheetOpenChange: (open: boolean) => void;
}

// The capture filter controls — one component for both layouts (matches the
// PricingFilters sibling): a desktop inline row, and on mobile a filter bar +
// bottom sheet. The desktop bespoke `.debugger-filters` classes are kept.
export function CaptureFilters({
  isMobile,
  showMobileDetail,
  filters,
  onFiltersChange,
  availablePlayers,
  availableEmotions,
  labelStats,
  loading,
  onRefresh,
  onToggleLabel,
  activeFilterCount,
  filterSheetOpen,
  onFilterSheetOpenChange,
}: CaptureFiltersProps) {
  const playerOptions = (
    <>
      <option value="">All Players</option>
      {availablePlayers.map((p) => (
        <option key={p} value={p}>
          {p}
        </option>
      ))}
    </>
  );

  if (isMobile) {
    return (
      <>
        {!showMobileDetail && (
          <MobileFilterBar
            activeFilterCount={activeFilterCount}
            onFilterClick={() => onFilterSheetOpenChange(true)}
            actions={
              <button
                className="mobile-filter-bar__icon-btn"
                onClick={onRefresh}
                disabled={loading}
                type="button"
                aria-label="Refresh"
              >
                <RefreshCw size={20} className={loading ? 'spinning' : ''} />
              </button>
            }
          />
        )}
        <MobileFilterSheet
          isOpen={filterSheetOpen}
          onClose={() => onFilterSheetOpenChange(false)}
          title="Filters"
        >
          <FilterSheetContent
            accentColor="teal"
            onClear={() => {
              // Full reset, matching the desktop "Clear Filters" button — the
              // old partial object left action/phase/emotion/tilt set.
              onFiltersChange(DEFAULT_FILTERS);
              onFilterSheetOpenChange(false);
            }}
            onApply={() => onFilterSheetOpenChange(false)}
          >
            {/* Labels first — the primary way to find flagged decisions on mobile */}
            {labelStats && Object.keys(labelStats).length > 0 && (
              <FilterGroup label="Labels / Flags">
                <div className="debugger-filter-chips">
                  {Object.entries(labelStats)
                    .filter(([, count]) => count > 0)
                    .map(([label, count]) => (
                      <button
                        key={label}
                        className={`label-chip label-chip--${getLabelSeverity(label)} ${filters.labels?.includes(label) ? 'label-chip--selected' : ''}`}
                        onClick={() => onToggleLabel(label)}
                        type="button"
                      >
                        <span className="label-chip__count">{count}</span>
                        <span className="label-chip__name">{formatLabelName(label)}</span>
                      </button>
                    ))}
                </div>
              </FilterGroup>
            )}

            <FilterGroup label="Player">
              <select
                className="mobile-filter-sheet__select"
                value={filters.player_name || ''}
                onChange={(e) => {
                  onFiltersChange({
                    ...filters,
                    player_name: e.target.value || undefined,
                    offset: 0,
                  });
                }}
              >
                {playerOptions}
              </select>
            </FilterGroup>

            <FilterGroup label="Action">
              <select
                className="mobile-filter-sheet__select"
                value={filters.action || ''}
                onChange={(e) => {
                  onFiltersChange({ ...filters, action: e.target.value || undefined, offset: 0 });
                }}
              >
                <option value="">All Actions</option>
                <option value="fold">Fold</option>
                <option value="check">Check</option>
                <option value="call">Call</option>
                <option value="raise">Raise</option>
              </select>
            </FilterGroup>

            <FilterGroup label="Phase">
              <select
                className="mobile-filter-sheet__select"
                value={filters.phase || ''}
                onChange={(e) => {
                  onFiltersChange({ ...filters, phase: e.target.value || undefined, offset: 0 });
                }}
              >
                <option value="">All Phases</option>
                <option value="PRE_FLOP">Pre-Flop</option>
                <option value="FLOP">Flop</option>
                <option value="TURN">Turn</option>
                <option value="RIVER">River</option>
              </select>
            </FilterGroup>

            <FilterGroup label="Min Pot Odds">
              <input
                type="number"
                className="mobile-filter-sheet__input"
                placeholder="e.g., 3"
                value={filters.min_pot_odds || ''}
                onChange={(e) => {
                  onFiltersChange({
                    ...filters,
                    min_pot_odds: e.target.value ? parseFloat(e.target.value) : undefined,
                    offset: 0,
                  });
                }}
              />
            </FilterGroup>

            <FilterGroup label="Error Type">
              <select
                className="mobile-filter-sheet__select"
                value={filters.error_type || ''}
                onChange={(e) => {
                  onFiltersChange({
                    ...filters,
                    error_type: e.target.value || undefined,
                    offset: 0,
                  });
                }}
              >
                <option value="">All Error Types</option>
                <option value="malformed_json">Malformed JSON</option>
                <option value="missing_field">Missing Field</option>
                <option value="invalid_action">Invalid Action</option>
                <option value="semantic_error">Semantic Error</option>
              </select>
            </FilterGroup>

            <FilterGroup label="Error Status">
              <select
                className="mobile-filter-sheet__select"
                value={filters.has_error === undefined ? '' : filters.has_error.toString()}
                onChange={(e) => {
                  onFiltersChange({
                    ...filters,
                    has_error: e.target.value === '' ? undefined : e.target.value === 'true',
                    offset: 0,
                  });
                }}
              >
                <option value="">All</option>
                <option value="true">Has Error</option>
                <option value="false">No Error</option>
              </select>
            </FilterGroup>

            <FilterGroup label="Correction">
              <select
                className="mobile-filter-sheet__select"
                value={filters.is_correction === undefined ? '' : filters.is_correction.toString()}
                onChange={(e) => {
                  onFiltersChange({
                    ...filters,
                    is_correction: e.target.value === '' ? undefined : e.target.value === 'true',
                    offset: 0,
                  });
                }}
              >
                <option value="">All</option>
                <option value="false">Original Only</option>
                <option value="true">Corrections Only</option>
              </select>
            </FilterGroup>

            <FilterGroup label="Emotion">
              <select
                className="mobile-filter-sheet__select"
                value={filters.display_emotion || ''}
                onChange={(e) => {
                  onFiltersChange({
                    ...filters,
                    display_emotion: e.target.value || undefined,
                    offset: 0,
                  });
                }}
              >
                <option value="">All</option>
                {availableEmotions.map((e) => (
                  <option key={e} value={e}>
                    {formatEmotionName(e)}
                  </option>
                ))}
              </select>
            </FilterGroup>

            <FilterGroup label="Min Tilt">
              <input
                className="mobile-filter-sheet__input"
                type="number"
                placeholder="Min tilt level"
                value={filters.min_tilt_level ?? ''}
                onChange={(e) => {
                  onFiltersChange({
                    ...filters,
                    min_tilt_level: e.target.value ? parseFloat(e.target.value) : undefined,
                    offset: 0,
                  });
                }}
                min={0}
                max={1}
                step={0.1}
              />
            </FilterGroup>

            <FilterGroup label="Max Tilt">
              <input
                className="mobile-filter-sheet__input"
                type="number"
                placeholder="Max tilt level"
                value={filters.max_tilt_level ?? ''}
                onChange={(e) => {
                  onFiltersChange({
                    ...filters,
                    max_tilt_level: e.target.value ? parseFloat(e.target.value) : undefined,
                    offset: 0,
                  });
                }}
                min={0}
                max={1}
                step={0.1}
              />
            </FilterGroup>
          </FilterSheetContent>
        </MobileFilterSheet>
      </>
    );
  }

  // Desktop inline filter row
  return (
    <div className="debugger-filters">
      <select
        value={filters.player_name || ''}
        onChange={(e) =>
          onFiltersChange({ ...filters, player_name: e.target.value || undefined, offset: 0 })
        }
      >
        {playerOptions}
      </select>

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
