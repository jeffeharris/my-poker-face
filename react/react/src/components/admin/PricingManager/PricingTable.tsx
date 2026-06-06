import type { PivotedModel, PendingChange, SortDirection, TabType } from './types';
import { formatCostValue } from './pricingUtils';

interface PricingTableProps {
  models: PivotedModel[];
  units: readonly string[];
  unitLabels: Record<string, string>;
  sortColumn: string;
  sortDirection: SortDirection;
  onSort: (column: string) => void;
  selectedModel: PivotedModel | null;
  pendingChanges: Map<string, PendingChange>;
  onRowClick: (model: PivotedModel) => void;
  activeTab: TabType;
}

export function PricingTable({
  models,
  units,
  unitLabels,
  sortColumn,
  sortDirection,
  onSort,
  selectedModel,
  pendingChanges,
  onRowClick,
  activeTab,
}: PricingTableProps) {
  const SortIndicator = ({ column }: { column: string }) => {
    if (sortColumn !== column) return null;
    return <span className="prm-sort-indicator">{sortDirection === 'asc' ? '▲' : '▼'}</span>;
  };

  return (
    <div className="prm-table-wrapper">
      <table
        className={`prm-table prm-table--clickable ${activeTab === 'image' ? 'prm-table--wide' : ''}`}
      >
        <thead>
          <tr>
            <th className="prm-table__th--sortable" onClick={() => onSort('provider')}>
              Provider <SortIndicator column="provider" />
            </th>
            <th className="prm-table__th--sortable" onClick={() => onSort('model')}>
              Model <SortIndicator column="model" />
            </th>
            {units.map((unit) => (
              <th
                key={unit}
                className="prm-table__th--sortable prm-table__th--cost"
                onClick={() => onSort(unit)}
              >
                {unitLabels[unit]} <SortIndicator column={unit} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {models.length === 0 ? (
            <tr>
              <td colSpan={2 + units.length} className="prm-table__empty">
                No {activeTab} models found
              </td>
            </tr>
          ) : (
            models.map((model) => {
              const isSelected =
                selectedModel?.provider === model.provider && selectedModel?.model === model.model;
              const modelKey = `${model.provider}::${model.model}`;
              const hasPending = pendingChanges.has(modelKey);
              return (
                <tr
                  key={modelKey}
                  className={`prm-table__row--clickable ${
                    isSelected ? 'prm-table__row--selected' : ''
                  } ${hasPending ? 'prm-table__row--pending' : ''}`}
                  onClick={() => onRowClick(model)}
                >
                  <td>{model.provider}</td>
                  <td className="prm-table__model">{model.model}</td>
                  {units.map((unit) => (
                    <td
                      key={unit}
                      className={`prm-table__cost ${
                        model.costs[unit] === null || model.costs[unit] === undefined
                          ? 'prm-table__cell--empty'
                          : ''
                      }`}
                    >
                      {formatCostValue(model.costs[unit])}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
