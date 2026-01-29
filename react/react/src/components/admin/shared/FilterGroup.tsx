import type { ReactNode } from 'react';

interface FilterGroupProps {
  /** Label displayed above the filter control */
  label: string;
  /** Filter control (select, input, toggle, chips, etc.) */
  children: ReactNode;
}

/**
 * FilterGroup - Shared label + control wrapper for filter sheet content.
 */
export function FilterGroup({ label, children }: FilterGroupProps) {
  return (
    <div className="mobile-filter-sheet__group">
      <label className="mobile-filter-sheet__group-label">{label}</label>
      {children}
    </div>
  );
}

export default FilterGroup;
