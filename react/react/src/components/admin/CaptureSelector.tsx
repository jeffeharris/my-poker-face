import { useState, useEffect, useCallback } from 'react';
import { adminAPI } from '../../utils/api';
import { Search, Tag, Filter, ChevronDown, ChevronUp, Check, X, Plus, Trash2 } from 'lucide-react';
import './AdminShared.css';
import './CaptureSelector.css';

// ============================================
// Types
// ============================================

interface CaptureLabel {
  label: string;
  label_type: string;
  created_at: string;
}

interface Capture {
  id: number;
  created_at: string;
  game_id: string | null;
  player_name: string;
  hand_number: number | null;
  phase: string;
  action_taken: string;
  pot_total: number;
  cost_to_call: number;
  pot_odds: number | null;
  player_stack: number;
  community_cards: string[] | null;
  player_hand: string[] | null;
  model: string;
  provider: string;
  latency_ms: number;
  tags: string[] | null;
  notes: string | null;
  labels?: CaptureLabel[];
  // Error/correction resilience fields
  error_type?: string | null;
  parent_id?: number | null;
  correction_attempt?: number | null;
}

interface LabelInfo {
  name: string;
  count: number;
  label_type: string;
}

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

interface CaptureSelectorProps {
  embedded?: boolean;
  selectionMode?: boolean;
  selectedIds?: number[];
  onSelectionChange?: (ids: number[]) => void;
}

// ============================================
// Constants
// ============================================

const PHASES = ['PRE_FLOP', 'FLOP', 'TURN', 'RIVER'];
const ACTIONS = ['fold', 'check', 'call', 'raise'];

// ============================================
// Main Component
// ============================================

export function CaptureSelector({
  embedded = false,
  selectionMode = false,
  selectedIds = [],
  onSelectionChange,
}: CaptureSelectorProps) {
  // Data state
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [totalCaptures, setTotalCaptures] = useState(0);
  const [allLabels, setAllLabels] = useState<LabelInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [alert, setAlert] = useState<AlertState | null>(null);

  // Filter state
  const [selectedLabels, setSelectedLabels] = useState<string[]>([]);
  const [phaseFilter, setPhaseFilter] = useState<string>('');
  const [actionFilter, setActionFilter] = useState<string>('');
  const [minPotOdds, setMinPotOdds] = useState<string>('');
  const [maxPotOdds, setMaxPotOdds] = useState<string>('');
  const [matchAllLabels, setMatchAllLabels] = useState(false);
  // Error/correction resilience filters
  const [errorTypeFilter, setErrorTypeFilter] = useState<string>('');
  const [hasErrorFilter, setHasErrorFilter] = useState<string>('');  // '', 'true', 'false'
  const [isCorrectionFilter, setIsCorrectionFilter] = useState<string>('');  // '', 'true', 'false'

  // Pagination
  const [page, setPage] = useState(0);
  const [pageSize] = useState(20);

  // UI state
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [showFilters, setShowFilters] = useState(false);
  const [showLabelDropdown, setShowLabelDropdown] = useState(false);

  // Bulk label state
  const [bulkLabelInput, setBulkLabelInput] = useState('');
  const [showBulkActions, setShowBulkActions] = useState(false);

  // Selection state (internal if not controlled externally)
  const [internalSelectedIds, setInternalSelectedIds] = useState<number[]>([]);
  const selected = selectionMode && onSelectionChange ? selectedIds : internalSelectedIds;
  const setSelected = selectionMode && onSelectionChange ? onSelectionChange : setInternalSelectedIds;

  // Fetch captures with current filters
  const fetchCaptures = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();

      if (selectedLabels.length > 0) {
        params.append('labels', selectedLabels.join(','));
        params.append('match_all', matchAllLabels ? 'true' : 'false');
      }
      if (phaseFilter) params.append('phase', phaseFilter);
      if (actionFilter) params.append('action', actionFilter);
      if (minPotOdds) params.append('min_pot_odds', minPotOdds);
      if (maxPotOdds) params.append('max_pot_odds', maxPotOdds);
      if (errorTypeFilter) params.append('error_type', errorTypeFilter);
      if (hasErrorFilter) params.append('has_error', hasErrorFilter);
      if (isCorrectionFilter) params.append('is_correction', isCorrectionFilter);
      params.append('limit', String(pageSize));
      params.append('offset', String(page * pageSize));

      const response = await adminAPI.fetch(`/api/captures/search?${params}`);
      const data = await response.json();

      if (data.success) {
        setCaptures(data.captures);
        setTotalCaptures(data.total);
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to load captures' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setLoading(false);
    }
  }, [selectedLabels, phaseFilter, actionFilter, minPotOdds, maxPotOdds, matchAllLabels, errorTypeFilter, hasErrorFilter, isCorrectionFilter, page, pageSize]);

  // Fetch all labels for dropdown
  const fetchLabels = useCallback(async () => {
    try {
      const response = await adminAPI.fetch('/api/capture-labels');
      const data = await response.json();
      if (data.success) {
        setAllLabels(data.labels);
      }
    } catch {
      // Silent fail for labels
    }
  }, []);

  useEffect(() => {
    fetchCaptures();
  }, [fetchCaptures]);

  useEffect(() => {
    fetchLabels();
  }, [fetchLabels]);

  // Clear alert after timeout
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 4000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  // Reset page when filters change
  useEffect(() => {
    setPage(0);
  }, [selectedLabels, phaseFilter, actionFilter, minPotOdds, maxPotOdds, matchAllLabels, errorTypeFilter, hasErrorFilter, isCorrectionFilter]);

  // Toggle label selection
  const toggleLabel = (label: string) => {
    setSelectedLabels(prev =>
      prev.includes(label)
        ? prev.filter(l => l !== label)
        : [...prev, label]
    );
  };

  // Clear all filters
  const clearFilters = () => {
    setSelectedLabels([]);
    setPhaseFilter('');
    setActionFilter('');
    setMinPotOdds('');
    setMaxPotOdds('');
    setMatchAllLabels(false);
    setErrorTypeFilter('');
    setHasErrorFilter('');
    setIsCorrectionFilter('');
  };

  // Toggle capture selection
  const toggleCaptureSelection = (id: number) => {
    setSelected(
      selected.includes(id)
        ? selected.filter(i => i !== id)
        : [...selected, id]
    );
  };

  // Select all on current page
  const selectAllOnPage = () => {
    const pageIds = captures.map(c => c.id);
    const newSelected = [...new Set([...selected, ...pageIds])];
    setSelected(newSelected);
  };

  // Deselect all
  const deselectAll = () => {
    setSelected([]);
  };

  // Add label to single capture
  const addLabelToCapture = async (captureId: number, label: string) => {
    try {
      const response = await adminAPI.fetch(`/api/captures/${captureId}/labels`, {
        method: 'POST',
        body: JSON.stringify({ add: [label] }),
      });
      const data = await response.json();
      if (data.success) {
        fetchCaptures();
        fetchLabels();
        setAlert({ type: 'success', message: `Added label "${label}"` });
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to add label' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  // Remove label from single capture
  const removeLabelFromCapture = async (captureId: number, label: string) => {
    try {
      const response = await adminAPI.fetch(`/api/captures/${captureId}/labels`, {
        method: 'POST',
        body: JSON.stringify({ remove: [label] }),
      });
      const data = await response.json();
      if (data.success) {
        fetchCaptures();
        fetchLabels();
        setAlert({ type: 'success', message: `Removed label "${label}"` });
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to remove label' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  // Bulk add labels
  const bulkAddLabels = async () => {
    if (!bulkLabelInput.trim() || selected.length === 0) return;

    const labels = bulkLabelInput.split(',').map(l => l.trim()).filter(Boolean);
    try {
      const response = await adminAPI.fetch('/api/captures/bulk-labels', {
        method: 'POST',
        body: JSON.stringify({ capture_ids: selected, add: labels }),
      });
      const data = await response.json();
      if (data.success) {
        fetchCaptures();
        fetchLabels();
        setBulkLabelInput('');
        setAlert({
          type: 'success',
          message: `Added ${data.added.labels_added} label(s) to ${data.added.captures_affected} capture(s)`,
        });
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to bulk add labels' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  // Bulk remove labels
  const bulkRemoveLabels = async (labels: string[]) => {
    if (labels.length === 0 || selected.length === 0) return;

    try {
      const response = await adminAPI.fetch('/api/captures/bulk-labels', {
        method: 'POST',
        body: JSON.stringify({ capture_ids: selected, remove: labels }),
      });
      const data = await response.json();
      if (data.success) {
        fetchCaptures();
        fetchLabels();
        setAlert({
          type: 'success',
          message: `Removed ${data.removed.labels_removed} label(s)`,
        });
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to bulk remove labels' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  const totalPages = Math.ceil(totalCaptures / pageSize);
  const hasActiveFilters = selectedLabels.length > 0 || phaseFilter || actionFilter || minPotOdds || maxPotOdds || errorTypeFilter || hasErrorFilter || isCorrectionFilter;

  if (loading && captures.length === 0) {
    return (
      <div className="cs-loading">
        <div className="cs-loading__spinner" />
        <span>Loading captures...</span>
      </div>
    );
  }

  return (
    <div className={`cs-container ${embedded ? 'cs-container--embedded' : ''}`}>
      {/* Alert Toast */}
      {alert && (
        <div className={`cs-alert cs-alert--${alert.type}`}>
          <span className="cs-alert__icon">
            {alert.type === 'success' ? '✓' : alert.type === 'error' ? '✕' : 'i'}
          </span>
          <span className="cs-alert__message">{alert.message}</span>
          <button className="cs-alert__close" onClick={() => setAlert(null)}>×</button>
        </div>
      )}

      {/* Header */}
      <div className="cs-header">
        <div className="cs-header__text">
          <h2 className="cs-header__title">Capture Selector</h2>
          <p className="cs-header__subtitle">
            {totalCaptures} captured AI decisions
            {selected.length > 0 && ` • ${selected.length} selected`}
          </p>
        </div>
        <button
          className={`cs-header__filter-toggle ${showFilters ? 'cs-header__filter-toggle--active' : ''}`}
          onClick={() => setShowFilters(!showFilters)}
        >
          <Filter size={18} />
          Filters
          {hasActiveFilters && <span className="cs-filter-badge">{
            [selectedLabels.length > 0, phaseFilter, actionFilter, minPotOdds || maxPotOdds].filter(Boolean).length
          }</span>}
          {showFilters ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
      </div>

      {/* Filters Panel */}
      {showFilters && (
        <div className="cs-filters">
          {/* Label Filter */}
          <div className="cs-filter-group">
            <label className="cs-filter-label">Labels</label>
            <div className="cs-label-selector">
              <button
                className="cs-label-dropdown-trigger"
                onClick={() => setShowLabelDropdown(!showLabelDropdown)}
              >
                <Tag size={16} />
                {selectedLabels.length === 0
                  ? 'Select labels...'
                  : `${selectedLabels.length} label(s) selected`}
                <ChevronDown size={16} />
              </button>
              {showLabelDropdown && (
                <div className="cs-label-dropdown">
                  {allLabels.length === 0 ? (
                    <div className="cs-label-dropdown__empty">No labels yet</div>
                  ) : (
                    allLabels.map(label => (
                      <button
                        key={label.name}
                        className={`cs-label-option ${selectedLabels.includes(label.name) ? 'cs-label-option--selected' : ''}`}
                        onClick={() => toggleLabel(label.name)}
                      >
                        <span className="cs-label-option__check">
                          {selectedLabels.includes(label.name) && <Check size={14} />}
                        </span>
                        <span className="cs-label-option__name">{label.name}</span>
                        <span className="cs-label-option__count">{label.count}</span>
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
            {selectedLabels.length > 1 && (
              <label className="cs-match-all">
                <input
                  type="checkbox"
                  checked={matchAllLabels}
                  onChange={(e) => setMatchAllLabels(e.target.checked)}
                />
                <span>Match all labels</span>
              </label>
            )}
          </div>

          {/* Phase Filter */}
          <div className="cs-filter-group">
            <label className="cs-filter-label">Phase</label>
            <select
              className="cs-select"
              value={phaseFilter}
              onChange={(e) => setPhaseFilter(e.target.value)}
            >
              <option value="">All phases</option>
              {PHASES.map(phase => (
                <option key={phase} value={phase}>{phase}</option>
              ))}
            </select>
          </div>

          {/* Action Filter */}
          <div className="cs-filter-group">
            <label className="cs-filter-label">Action</label>
            <select
              className="cs-select"
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
            >
              <option value="">All actions</option>
              {ACTIONS.map(action => (
                <option key={action} value={action}>{action}</option>
              ))}
            </select>
          </div>

          {/* Pot Odds Range */}
          <div className="cs-filter-group cs-filter-group--range">
            <label className="cs-filter-label">Pot Odds</label>
            <div className="cs-range-inputs">
              <input
                type="number"
                className="cs-input cs-input--small"
                placeholder="Min"
                value={minPotOdds}
                onChange={(e) => setMinPotOdds(e.target.value)}
                step="0.1"
                min="0"
              />
              <span className="cs-range-separator">to</span>
              <input
                type="number"
                className="cs-input cs-input--small"
                placeholder="Max"
                value={maxPotOdds}
                onChange={(e) => setMaxPotOdds(e.target.value)}
                step="0.1"
                min="0"
              />
            </div>
          </div>

          {/* Error Type Filter */}
          <div className="cs-filter-group">
            <label className="cs-filter-label">Error Type</label>
            <select
              className="cs-select"
              value={errorTypeFilter}
              onChange={(e) => setErrorTypeFilter(e.target.value)}
            >
              <option value="">All error types</option>
              <option value="malformed_json">Malformed JSON</option>
              <option value="missing_field">Missing Field</option>
              <option value="invalid_action">Invalid Action</option>
              <option value="semantic_error">Semantic Error</option>
            </select>
          </div>

          {/* Has Error Filter */}
          <div className="cs-filter-group">
            <label className="cs-filter-label">Error Status</label>
            <select
              className="cs-select"
              value={hasErrorFilter}
              onChange={(e) => setHasErrorFilter(e.target.value)}
            >
              <option value="">All</option>
              <option value="true">Has Error</option>
              <option value="false">No Error</option>
            </select>
          </div>

          {/* Is Correction Filter */}
          <div className="cs-filter-group">
            <label className="cs-filter-label">Correction</label>
            <select
              className="cs-select"
              value={isCorrectionFilter}
              onChange={(e) => setIsCorrectionFilter(e.target.value)}
            >
              <option value="">All</option>
              <option value="false">Original Only</option>
              <option value="true">Corrections Only</option>
            </select>
          </div>

          {/* Clear Filters */}
          {hasActiveFilters && (
            <button className="cs-clear-filters" onClick={clearFilters}>
              <X size={14} />
              Clear filters
            </button>
          )}
        </div>
      )}

      {/* Selected Labels Pills */}
      {selectedLabels.length > 0 && (
        <div className="cs-selected-labels">
          {selectedLabels.map(label => (
            <span key={label} className="cs-label-pill">
              {label}
              <button onClick={() => toggleLabel(label)}>
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Selection Actions */}
      {selected.length > 0 && (
        <div className="cs-selection-bar">
          <span className="cs-selection-bar__count">{selected.length} selected</span>
          <div className="cs-selection-bar__actions">
            <button
              className="cs-btn cs-btn--ghost"
              onClick={() => setShowBulkActions(!showBulkActions)}
            >
              <Tag size={16} />
              Bulk Label
              {showBulkActions ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            <button className="cs-btn cs-btn--ghost" onClick={selectAllOnPage}>
              Select all on page
            </button>
            <button className="cs-btn cs-btn--ghost cs-btn--danger" onClick={deselectAll}>
              Deselect all
            </button>
          </div>
        </div>
      )}

      {/* Bulk Actions Panel */}
      {showBulkActions && selected.length > 0 && (
        <div className="cs-bulk-actions">
          <div className="cs-bulk-add">
            <input
              type="text"
              className="cs-input"
              placeholder="Enter labels (comma-separated)..."
              value={bulkLabelInput}
              onChange={(e) => setBulkLabelInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && bulkAddLabels()}
            />
            <button
              className="cs-btn cs-btn--primary"
              onClick={bulkAddLabels}
              disabled={!bulkLabelInput.trim()}
            >
              <Plus size={16} />
              Add
            </button>
          </div>
          {allLabels.length > 0 && (
            <div className="cs-bulk-remove">
              <span className="cs-bulk-remove__label">Remove from selected:</span>
              <div className="cs-bulk-remove__labels">
                {allLabels.slice(0, 10).map(label => (
                  <button
                    key={label.name}
                    className="cs-remove-label-btn"
                    onClick={() => bulkRemoveLabels([label.name])}
                    title={`Remove "${label.name}" from selected`}
                  >
                    <Trash2 size={12} />
                    {label.name}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Captures List */}
      <div className="cs-list">
        {captures.length === 0 ? (
          <div className="cs-empty">
            <Search size={48} />
            <p>{hasActiveFilters ? 'No captures match your filters' : 'No captures found'}</p>
          </div>
        ) : (
          captures.map(capture => (
            <div
              key={capture.id}
              className={`cs-capture ${expandedId === capture.id ? 'cs-capture--expanded' : ''} ${selected.includes(capture.id) ? 'cs-capture--selected' : ''}`}
            >
              <div className="cs-capture__header" onClick={() => setExpandedId(expandedId === capture.id ? null : capture.id)}>
                {selectionMode && (
                  <label className="cs-capture__checkbox" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selected.includes(capture.id)}
                      onChange={() => toggleCaptureSelection(capture.id)}
                    />
                  </label>
                )}
                <div className="cs-capture__summary">
                  <span className="cs-capture__player">{capture.player_name}</span>
                  <span className={`cs-capture__action cs-capture__action--${capture.action_taken}`}>
                    {capture.action_taken}
                  </span>
                  <span className="cs-capture__phase">{capture.phase}</span>
                  {capture.pot_odds !== null && (
                    <span className="cs-capture__odds">{capture.pot_odds.toFixed(2)} odds</span>
                  )}
                </div>
                <div className="cs-capture__meta">
                  <span className="cs-capture__model">{capture.model}</span>
                  <span className="cs-capture__date">
                    {new Date(capture.created_at).toLocaleDateString()}
                  </span>
                </div>
                <span className="cs-capture__chevron">
                  {expandedId === capture.id ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
                </span>
              </div>

              {/* Labels Row */}
              {capture.labels && capture.labels.length > 0 && (
                <div className="cs-capture__labels">
                  {capture.labels.map(label => (
                    <span
                      key={label.label}
                      className={`cs-capture-label cs-capture-label--${label.label_type}`}
                    >
                      {label.label}
                      <button
                        className="cs-capture-label__remove"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeLabelFromCapture(capture.id, label.label);
                        }}
                      >
                        <X size={10} />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              {/* Error/Correction Info */}
              {capture.error_type && (
                <div className="cs-capture__error">
                  <span className="cs-error-badge">{capture.error_type.replace(/_/g, ' ')}</span>
                  {capture.correction_attempt != null && capture.correction_attempt > 0 && (
                    <span className="cs-correction-badge">Attempt #{capture.correction_attempt}</span>
                  )}
                </div>
              )}

              {/* Expanded Details */}
              {expandedId === capture.id && (
                <div className="cs-capture__details">
                  <div className="cs-detail-grid">
                    <div className="cs-detail-item">
                      <span className="cs-detail-item__label">Pot Total</span>
                      <span className="cs-detail-item__value">{capture.pot_total}</span>
                    </div>
                    <div className="cs-detail-item">
                      <span className="cs-detail-item__label">Cost to Call</span>
                      <span className="cs-detail-item__value">{capture.cost_to_call}</span>
                    </div>
                    <div className="cs-detail-item">
                      <span className="cs-detail-item__label">Stack</span>
                      <span className="cs-detail-item__value">{capture.player_stack}</span>
                    </div>
                    <div className="cs-detail-item">
                      <span className="cs-detail-item__label">Latency</span>
                      <span className="cs-detail-item__value">{capture.latency_ms}ms</span>
                    </div>
                    {capture.community_cards && capture.community_cards.length > 0 && (
                      <div className="cs-detail-item cs-detail-item--full">
                        <span className="cs-detail-item__label">Community Cards</span>
                        <span className="cs-detail-item__value">{capture.community_cards.join(' ')}</span>
                      </div>
                    )}
                    {capture.player_hand && capture.player_hand.length > 0 && (
                      <div className="cs-detail-item cs-detail-item--full">
                        <span className="cs-detail-item__label">Hand</span>
                        <span className="cs-detail-item__value">{capture.player_hand.join(' ')}</span>
                      </div>
                    )}
                  </div>

                  {/* Quick Add Label */}
                  <div className="cs-quick-label">
                    <input
                      type="text"
                      className="cs-input cs-input--small"
                      placeholder="Add label..."
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && e.currentTarget.value.trim()) {
                          addLabelToCapture(capture.id, e.currentTarget.value.trim());
                          e.currentTarget.value = '';
                        }
                      }}
                    />
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="cs-pagination">
          <button
            className="cs-pagination__btn"
            onClick={() => setPage(Math.max(0, page - 1))}
            disabled={page === 0}
          >
            Previous
          </button>
          <span className="cs-pagination__info">
            Page {page + 1} of {totalPages}
          </span>
          <button
            className="cs-pagination__btn"
            onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
            disabled={page >= totalPages - 1}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}

export default CaptureSelector;
