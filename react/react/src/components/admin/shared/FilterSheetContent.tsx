import type { ReactNode } from 'react';

interface FilterSheetContentProps {
  /** Filter groups to render */
  children: ReactNode;
  /** Callback when "Clear All" is clicked */
  onClear: () => void;
  /** Callback when "Apply" is clicked */
  onApply: () => void;
  /** Accent color for the Apply button: 'gold' (default) or 'teal' */
  accentColor?: 'gold' | 'teal';
}

/**
 * FilterSheetContent - Shared wrapper for filter sheet internals.
 *
 * Renders filter groups followed by a divider and Clear All / Apply action buttons.
 */
export function FilterSheetContent({ children, onClear, onApply, accentColor = 'gold' }: FilterSheetContentProps) {
  return (
    <div className="mobile-filter-sheet__body">
      {children}
      <div className="mobile-filter-sheet__actions">
        <button
          className="mobile-filter-sheet__clear-btn"
          onClick={onClear}
          type="button"
        >
          Clear All
        </button>
        <button
          className={`mobile-filter-sheet__apply-btn mobile-filter-sheet__apply-btn--${accentColor}`}
          onClick={onApply}
          type="button"
        >
          Apply
        </button>
      </div>
    </div>
  );
}

export default FilterSheetContent;
