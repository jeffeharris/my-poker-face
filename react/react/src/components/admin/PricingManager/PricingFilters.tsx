import { Plus } from 'lucide-react';
import { MobileFilterSheet } from '../shared/MobileFilterSheet';
import { MobileFilterBar } from '../shared/MobileFilterBar';
import { FilterSheetContent } from '../shared/FilterSheetContent';
import { FilterGroup } from '../shared/FilterGroup';

interface PricingFiltersProps {
  isMobile: boolean;
  filterProvider: string;
  setFilterProvider: (value: string) => void;
  searchQuery: string;
  setSearchQuery: (value: string) => void;
  currentOnly: boolean;
  setCurrentOnly: (value: boolean) => void;
  filteredProviders: string[];
  filterSheetOpen: boolean;
  setFilterSheetOpen: (open: boolean) => void;
  activeFilterCount: number;
  onAddEntry: () => void;
}

export function PricingFilters({
  isMobile,
  filterProvider,
  setFilterProvider,
  searchQuery,
  setSearchQuery,
  currentOnly,
  setCurrentOnly,
  filteredProviders,
  filterSheetOpen,
  setFilterSheetOpen,
  activeFilterCount,
  onAddEntry,
}: PricingFiltersProps) {
  if (isMobile) {
    return (
      <>
        <MobileFilterBar
          activeFilterCount={activeFilterCount}
          onFilterClick={() => setFilterSheetOpen(true)}
          actions={
            <button
              className="mobile-filter-bar__icon-btn"
              onClick={onAddEntry}
              type="button"
              aria-label="Add entry"
            >
              <Plus size={20} />
            </button>
          }
        />
        <MobileFilterSheet
          isOpen={filterSheetOpen}
          onClose={() => setFilterSheetOpen(false)}
          title="Filters"
        >
          <FilterSheetContent
            onClear={() => {
              setFilterProvider('');
              setSearchQuery('');
              setCurrentOnly(true);
              setFilterSheetOpen(false);
            }}
            onApply={() => setFilterSheetOpen(false)}
          >
            <FilterGroup label="Provider">
              <select
                className="mobile-filter-sheet__select"
                value={filterProvider}
                onChange={(e) => setFilterProvider(e.target.value)}
              >
                <option value="">All Providers</option>
                {filteredProviders.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </FilterGroup>
            <FilterGroup label="Search">
              <input
                type="text"
                className="mobile-filter-sheet__input"
                placeholder="Search models..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </FilterGroup>
            <FilterGroup label="">
              <label className="mobile-filter-sheet__toggle">
                <input
                  type="checkbox"
                  checked={currentOnly}
                  onChange={(e) => setCurrentOnly(e.target.checked)}
                />
                <span>Enabled models only</span>
              </label>
            </FilterGroup>
          </FilterSheetContent>
        </MobileFilterSheet>
      </>
    );
  }

  return (
    <div className="prm-filters">
      <select
        className="prm-select"
        value={filterProvider}
        onChange={(e) => setFilterProvider(e.target.value)}
      >
        <option value="">All Providers</option>
        {filteredProviders.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>
      <input
        type="text"
        className="prm-input prm-filters__search"
        placeholder="Search models..."
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
      />
      <label className="prm-checkbox">
        <input
          type="checkbox"
          checked={currentOnly}
          onChange={(e) => setCurrentOnly(e.target.checked)}
        />
        <span>Enabled models</span>
      </label>
    </div>
  );
}
