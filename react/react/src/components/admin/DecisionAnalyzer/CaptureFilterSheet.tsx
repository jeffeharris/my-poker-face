import { MobileFilterSheet } from '../shared/MobileFilterSheet';
import { FilterSheetContent } from '../shared/FilterSheetContent';
import { FilterGroup } from '../shared/FilterGroup';
import type { CaptureFilters, LabelStats } from './types';
import { formatEmotionName, formatLabelName, getLabelSeverity } from './utils';

interface CaptureFilterSheetProps {
  isOpen: boolean;
  onClose: () => void;
  filters: CaptureFilters;
  onFiltersChange: (filters: CaptureFilters) => void;
  availableEmotions: string[];
  labelStats: LabelStats | null;
  onToggleLabel: (label: string) => void;
}

// Mobile filter sheet (the desktop equivalent is DesktopFilterBar).
export function CaptureFilterSheet({
  isOpen,
  onClose,
  filters,
  onFiltersChange,
  availableEmotions,
  labelStats,
  onToggleLabel,
}: CaptureFilterSheetProps) {
  return (
    <MobileFilterSheet isOpen={isOpen} onClose={onClose} title="Filters">
      <FilterSheetContent
        accentColor="teal"
        onClear={() => {
          onFiltersChange({
            limit: 50,
            offset: 0,
            labels: undefined,
            error_type: undefined,
            has_error: undefined,
            is_correction: undefined,
          });
          onClose();
        }}
        onApply={onClose}
      >
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
              onFiltersChange({ ...filters, error_type: e.target.value || undefined, offset: 0 });
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

        {/* Label filter chips */}
        {labelStats && Object.keys(labelStats).length > 0 && (
          <FilterGroup label="Labels">
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
      </FilterSheetContent>
    </MobileFilterSheet>
  );
}
