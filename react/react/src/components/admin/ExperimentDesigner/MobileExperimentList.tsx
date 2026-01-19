import { useState, useCallback, useRef, useEffect } from 'react';
import { Plus, Filter, RefreshCw, Check, Beaker } from 'lucide-react';
import { ExperimentCard } from './ExperimentCard';
import { MobileFilterSheet } from '../shared/MobileFilterSheet';
import type { ExperimentSummary } from './types';
import type { ExperimentStatus } from './experimentStatus';
import './MobileExperimentList.css';

interface MobileExperimentListProps {
  experiments: ExperimentSummary[];
  loading: boolean;
  error: string | null;
  statusFilter: ExperimentStatus | 'all';
  onStatusFilterChange: (status: ExperimentStatus | 'all') => void;
  onRefresh: () => void;
  onViewExperiment: (experiment: ExperimentSummary) => void;
  onNewExperiment: () => void;
}

// Filter options with display labels
const FILTER_OPTIONS: { value: ExperimentStatus | 'all'; label: string }[] = [
  { value: 'all', label: 'All Experiments' },
  { value: 'pending', label: 'Pending' },
  { value: 'running', label: 'Running' },
  { value: 'paused', label: 'Paused' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
];

/**
 * MobileExperimentList - Mobile-optimized experiment list
 *
 * Features:
 * - Card-based layout
 * - Pull-to-refresh
 * - Bottom sheet filter
 * - Floating action button for new experiment
 * - Loading/error/empty states
 */
export function MobileExperimentList({
  experiments,
  loading,
  error,
  statusFilter,
  onStatusFilterChange,
  onRefresh,
  onViewExperiment,
  onNewExperiment,
}: MobileExperimentListProps) {
  const [filterSheetOpen, setFilterSheetOpen] = useState(false);
  const [isPulling, setIsPulling] = useState(false);
  const [pullDistance, setPullDistance] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const startYRef = useRef(0);
  const isPullingRef = useRef(false);

  // Pull-to-refresh threshold
  const PULL_THRESHOLD = 80;

  // Handle touch start
  const handleTouchStart = useCallback((e: TouchEvent) => {
    if (!listRef.current) return;
    // Only allow pull if scrolled to top
    if (listRef.current.scrollTop === 0) {
      startYRef.current = e.touches[0].clientY;
      isPullingRef.current = true;
    }
  }, []);

  // Handle touch move
  const handleTouchMove = useCallback((e: TouchEvent) => {
    if (!isPullingRef.current || loading) return;

    const currentY = e.touches[0].clientY;
    const diff = currentY - startYRef.current;

    if (diff > 0) {
      // Prevent default scroll and show pull indicator
      e.preventDefault();
      // Apply resistance to pull
      const resistance = 0.4;
      const distance = Math.min(diff * resistance, PULL_THRESHOLD * 1.5);
      setPullDistance(distance);
      setIsPulling(true);
    }
  }, [loading]);

  // Handle touch end
  const handleTouchEnd = useCallback(() => {
    if (!isPullingRef.current) return;

    if (pullDistance >= PULL_THRESHOLD && !loading) {
      onRefresh();
    }

    isPullingRef.current = false;
    setIsPulling(false);
    setPullDistance(0);
  }, [pullDistance, loading, onRefresh]);

  // Attach touch listeners
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;

    el.addEventListener('touchstart', handleTouchStart, { passive: true });
    el.addEventListener('touchmove', handleTouchMove, { passive: false });
    el.addEventListener('touchend', handleTouchEnd, { passive: true });

    return () => {
      el.removeEventListener('touchstart', handleTouchStart);
      el.removeEventListener('touchmove', handleTouchMove);
      el.removeEventListener('touchend', handleTouchEnd);
    };
  }, [handleTouchStart, handleTouchMove, handleTouchEnd]);

  // Get current filter label
  const currentFilterLabel = FILTER_OPTIONS.find(o => o.value === statusFilter)?.label || 'All';

  // Handle filter selection
  const handleFilterSelect = (value: ExperimentStatus | 'all') => {
    onStatusFilterChange(value);
    setFilterSheetOpen(false);
  };

  // Loading state
  if (loading && experiments.length === 0) {
    return (
      <div className="mobile-experiment-list__loading">
        <div className="mobile-experiment-list__spinner" />
        <span>Loading experiments...</span>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="mobile-experiment-list__error">
        <p>{error}</p>
        <button
          className="mobile-experiment-list__retry-btn"
          onClick={onRefresh}
          type="button"
        >
          <RefreshCw size={16} />
          Try Again
        </button>
      </div>
    );
  }

  return (
    <div className="mobile-experiment-list">
      {/* Filter bar */}
      <div className="mobile-experiment-list__filter-bar">
        <button
          className="mobile-experiment-list__filter-btn"
          onClick={() => setFilterSheetOpen(true)}
          type="button"
        >
          <Filter size={16} />
          <span>{currentFilterLabel}</span>
        </button>
        <button
          className="mobile-experiment-list__refresh-btn"
          onClick={onRefresh}
          disabled={loading}
          type="button"
          aria-label="Refresh"
        >
          <RefreshCw size={18} className={loading ? 'mobile-experiment-list__spin' : ''} />
        </button>
      </div>

      {/* Pull-to-refresh indicator */}
      {isPulling && (
        <div
          className="mobile-experiment-list__pull-indicator"
          style={{ height: pullDistance }}
        >
          <RefreshCw
            size={20}
            className={pullDistance >= PULL_THRESHOLD ? 'mobile-experiment-list__pull-ready' : ''}
            style={{
              transform: `rotate(${pullDistance * 2}deg)`,
              opacity: Math.min(1, pullDistance / PULL_THRESHOLD),
            }}
          />
        </div>
      )}

      {/* List content */}
      <div className="mobile-experiment-list__scroll" ref={listRef}>
        {experiments.length === 0 ? (
          <div className="mobile-experiment-list__empty">
            <div className="mobile-experiment-list__empty-icon">
              <Beaker size={32} />
            </div>
            <h3 className="mobile-experiment-list__empty-title">No experiments found</h3>
            <p className="mobile-experiment-list__empty-text">
              {statusFilter !== 'all'
                ? `No ${statusFilter} experiments. Try a different filter.`
                : 'Create your first experiment to get started.'}
            </p>
            <button
              className="mobile-experiment-list__empty-btn"
              onClick={onNewExperiment}
              type="button"
            >
              <Plus size={18} />
              New Experiment
            </button>
          </div>
        ) : (
          <div className="mobile-experiment-list__cards">
            {experiments.map((experiment, index) => (
              <div
                key={experiment.id}
                className="mobile-experiment-list__card-wrapper"
                style={{ animationDelay: `${Math.min(index * 30, 300)}ms` }}
              >
                <ExperimentCard
                  experiment={experiment}
                  onClick={() => onViewExperiment(experiment)}
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Floating action button */}
      <button
        className="mobile-experiment-list__fab"
        onClick={onNewExperiment}
        type="button"
        aria-label="Create new experiment"
      >
        <Plus size={24} />
      </button>

      {/* Filter bottom sheet */}
      <MobileFilterSheet
        isOpen={filterSheetOpen}
        onClose={() => setFilterSheetOpen(false)}
        title="Filter by Status"
      >
        <div className="mobile-experiment-list__filter-options">
          {FILTER_OPTIONS.map((option) => (
            <button
              key={option.value}
              className={`mobile-experiment-list__filter-option ${
                statusFilter === option.value ? 'mobile-experiment-list__filter-option--selected' : ''
              }`}
              onClick={() => handleFilterSelect(option.value)}
              type="button"
            >
              <span>{option.label}</span>
              {statusFilter === option.value && (
                <Check size={18} className="mobile-experiment-list__filter-check" />
              )}
            </button>
          ))}
        </div>
      </MobileFilterSheet>
    </div>
  );
}

export default MobileExperimentList;
