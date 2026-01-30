import type { ReactNode } from 'react';
import { Filter } from 'lucide-react';
import './MobileFilterBar.css';

interface MobileFilterBarProps {
  /** Number of active filters (shown as badge count) */
  activeFilterCount: number;
  /** Callback when filter button is clicked */
  onFilterClick: () => void;
  /** Optional action buttons to render on the right (add, refresh, etc.) */
  actions?: ReactNode;
}

/**
 * MobileFilterBar - Shared filter bar component for mobile admin pages.
 *
 * Renders a flex row with a filter button (flex: 1) and optional
 * action buttons on the right.
 */
export function MobileFilterBar({ activeFilterCount, onFilterClick, actions }: MobileFilterBarProps) {
  return (
    <div className="mobile-filter-bar">
      <button
        className="mobile-filter-bar__btn"
        onClick={onFilterClick}
        type="button"
        aria-label="Filters"
      >
        <Filter size={20} />
        <span>{activeFilterCount > 0 ? `Filters (${activeFilterCount})` : 'Filters'}</span>
      </button>
      {actions}
    </div>
  );
}

export default MobileFilterBar;
