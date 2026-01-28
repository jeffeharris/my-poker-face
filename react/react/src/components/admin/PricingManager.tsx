import { useState, useEffect, useCallback, useMemo, useRef, useImperativeHandle, forwardRef } from 'react';
import { adminAPI } from '../../utils/api';
import { logger } from '../../utils/logger';
import './PricingManager.css';

// ============================================
// Constants
// ============================================

const TEXT_UNITS = [
  'input_tokens_1m',
  'output_tokens_1m',
  'cached_input_tokens_1m',
  'reasoning_tokens_1m',
] as const;

const IMAGE_UNITS = [
  'image_512x512',
  'image_1024x1024',
  'image_1024x1792',
  'image_1792x1024',
  'image_512x512_hd',
  'image_1024x1024_hd',
  'image_1024x1792_hd',
  'image_1792x1024_hd',
] as const;

type TextUnit = (typeof TEXT_UNITS)[number];
type ImageUnit = (typeof IMAGE_UNITS)[number];

const TEXT_UNIT_LABELS: Record<TextUnit, string> = {
  input_tokens_1m: 'Input/1M',
  output_tokens_1m: 'Output/1M',
  cached_input_tokens_1m: 'Cached/1M',
  reasoning_tokens_1m: 'Reasoning/1M',
};

const IMAGE_UNIT_LABELS: Record<ImageUnit, string> = {
  image_512x512: '512x512',
  image_1024x1024: '1024x1024',
  image_1024x1792: '1024x1792',
  image_1792x1024: '1792x1024',
  image_512x512_hd: '512 HD',
  image_1024x1024_hd: '1024 HD',
  image_1024x1792_hd: '1024x1792 HD',
  image_1792x1024_hd: '1792x1024 HD',
};

// ============================================
// Types
// ============================================

interface PricingEntry {
  id: number;
  provider: string;
  model: string;
  unit: string;
  cost: number;
  valid_from: string | null;
  valid_until: string | null;
  notes: string | null;
}

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

interface PricingManagerProps {
  embedded?: boolean;
}

interface NewPricing {
  provider: string;
  model: string;
  unit: string;
  cost: string;
  notes: string;
}

interface PivotedModel {
  provider: string;
  model: string;
  costs: Record<string, number | null>;
  originalEntries: Record<string, PricingEntry>;
}

type TabType = 'text' | 'image';
type SortDirection = 'asc' | 'desc';

interface PendingChange {
  provider: string;
  model: string;
  values: Record<string, string>;
  validFrom: string;
}

interface SlideOutRef {
  isDirty: () => boolean;
  getValues: () => { values: Record<string, string>; validFrom: string };
}

// ============================================
// Helper Functions
// ============================================

function isTextUnit(unit: string): unit is TextUnit {
  return (TEXT_UNITS as readonly string[]).includes(unit);
}

function isImageUnit(unit: string): unit is ImageUnit {
  return (IMAGE_UNITS as readonly string[]).includes(unit);
}

function pivotPricingData(entries: PricingEntry[]): {
  textModels: PivotedModel[];
  imageModels: PivotedModel[];
} {
  const textMap = new Map<string, PivotedModel>();
  const imageMap = new Map<string, PivotedModel>();

  for (const entry of entries) {
    const key = `${entry.provider}::${entry.model}`;
    const isText = isTextUnit(entry.unit);
    const isImage = isImageUnit(entry.unit);

    if (!isText && !isImage) continue;

    const map = isText ? textMap : imageMap;

    if (!map.has(key)) {
      map.set(key, {
        provider: entry.provider,
        model: entry.model,
        costs: {},
        originalEntries: {},
      });
    }

    const pivoted = map.get(key)!;
    pivoted.costs[entry.unit] = entry.cost;
    pivoted.originalEntries[entry.unit] = entry;
  }

  return {
    textModels: Array.from(textMap.values()),
    imageModels: Array.from(imageMap.values()),
  };
}

function formatCostValue(cost: number | null | undefined): string {
  if (cost === null || cost === undefined) return '-';
  return `$${cost.toFixed(2)}`;
}

function getTodayISO(): string {
  return new Date().toISOString().split('T')[0];
}

// ============================================
// PricingSlideOut Component
// ============================================

interface SlideOutProps {
  model: PivotedModel;
  units: readonly string[];
  unitLabels: Record<string, string>;
  onClose: () => void;
  onSave: (values: Record<string, string>, validFrom: string) => Promise<void>;
  saving: boolean;
  pendingValues?: { values: Record<string, string>; validFrom: string } | null;
}

const PricingSlideOut = forwardRef<SlideOutRef, SlideOutProps>(function PricingSlideOut(
  {
    model,
    units,
    unitLabels,
    onClose,
    onSave,
    saving,
    pendingValues,
  },
  ref
) {
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [validFrom, setValidFrom] = useState(getTodayISO());

  // Reset state when model changes
  useEffect(() => {
    if (pendingValues) {
      setEditValues({ ...pendingValues.values });
      setValidFrom(pendingValues.validFrom);
    } else {
      const initial: Record<string, string> = {};
      for (const unit of units) {
        const cost = model.costs[unit];
        initial[unit] = cost !== null && cost !== undefined ? cost.toString() : '';
      }
      setEditValues(initial);
      setValidFrom(getTodayISO());
    }
  }, [model.provider, model.model, model.costs, pendingValues, units]);

  const isDirty = useMemo(() => {
    for (const unit of units) {
      const original = model.costs[unit];
      const current = editValues[unit];
      const originalStr = original !== null && original !== undefined ? original.toString() : '';
      if (current !== originalStr) return true;
    }
    return false;
  }, [editValues, model.costs, units]);

  // Expose methods to parent via ref
  useImperativeHandle(ref, () => ({
    isDirty: () => isDirty,
    getValues: () => ({ values: editValues, validFrom }),
  }), [isDirty, editValues, validFrom]);

  const handleSave = () => {
    onSave(editValues, validFrom);
  };

  return (
    <>
      <div className="prm-slideout-backdrop" onClick={onClose} />
      <div className="prm-slideout">
        <div className="prm-slideout__header">
          <div className="prm-slideout__title-row">
            <span className="prm-slideout__provider-badge">{model.provider}</span>
            <span className="prm-slideout__model-name">{model.model}</span>
          </div>
          <button className="prm-slideout__close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="prm-slideout__content">
          <div className="prm-form__group">
            <label>Valid From</label>
            <input
              type="date"
              className="prm-input"
              value={validFrom}
              onChange={(e) => setValidFrom(e.target.value)}
            />
          </div>

          <div className="prm-slideout__divider" />

          {units.map((unit) => (
            <div key={unit} className="prm-form__group">
              <label>{unitLabels[unit]}</label>
              <input
                type="number"
                className="prm-input"
                value={editValues[unit]}
                onChange={(e) =>
                  setEditValues((prev) => ({ ...prev, [unit]: e.target.value }))
                }
                onKeyDown={(e) => {
                  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                    e.preventDefault();
                  }
                }}
                placeholder="Not set"
                step="0.01"
                min="0"
              />
            </div>
          ))}
        </div>

        <div className="prm-slideout__footer">
          <div className="prm-slideout__hint">↑↓ Navigate models</div>
          <div className="prm-slideout__actions">
            <button className="prm-btn prm-btn--ghost" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button
              className="prm-btn prm-btn--primary"
              onClick={handleSave}
              disabled={!isDirty || saving}
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </>
  );
});

// ============================================
// Main Component
// ============================================

export function PricingManager({ embedded = false }: PricingManagerProps) {
  const [entries, setEntries] = useState<PricingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [filterProvider, setFilterProvider] = useState('');
  const [currentOnly, setCurrentOnly] = useState(true);
  const [providers, setProviders] = useState<string[]>([]);
  const [enabledModels, setEnabledModels] = useState<Set<string>>(new Set());
  const [newPricing, setNewPricing] = useState<NewPricing>({
    provider: '',
    model: '',
    unit: 'input_tokens_1m',
    cost: '',
    notes: '',
  });

  // New state for pivot table
  const [activeTab, setActiveTab] = useState<TabType>('text');
  const [selectedModel, setSelectedModel] = useState<PivotedModel | null>(null);
  const [saving, setSaving] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [sortColumn, setSortColumn] = useState<string>('provider');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  // Pending changes state
  const [pendingChanges, setPendingChanges] = useState<Map<string, PendingChange>>(new Map());
  const slideOutRef = useRef<SlideOutRef>(null);

  // Fetch pricing entries (always fetch current pricing)
  const fetchPricing = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (filterProvider) params.append('provider', filterProvider);
      params.append('current_only', 'true');

      const response = await adminAPI.fetch(`/admin/pricing?${params}`);
      const data = await response.json();

      if (data.success) {
        setEntries(data.pricing || []);
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to load pricing' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setLoading(false);
    }
  }, [filterProvider]);

  // Fetch providers
  const fetchProviders = useCallback(async () => {
    try {
      const response = await adminAPI.fetch('/admin/pricing/providers');
      const data = await response.json();
      if (data.success) {
        setProviders(data.providers.map((p: { provider: string }) => p.provider));
      }
    } catch (error) {
      logger.error('Failed to fetch providers:', error);
    }
  }, []);

  // Fetch enabled models
  const fetchEnabledModels = useCallback(async () => {
    try {
      const response = await adminAPI.fetch('/admin/api/models');
      const data = await response.json();
      if (data.success) {
        const enabled = new Set<string>();
        for (const model of data.models) {
          if (model.enabled) {
            enabled.add(`${model.provider}::${model.model}`);
          }
        }
        setEnabledModels(enabled);
      }
    } catch (error) {
      logger.error('Failed to fetch enabled models:', error);
    }
  }, []);

  useEffect(() => {
    fetchPricing();
    fetchProviders();
    fetchEnabledModels();
  }, [fetchPricing, fetchProviders, fetchEnabledModels]);

  // Pivot data
  const pivotedData = useMemo(() => pivotPricingData(entries), [entries]);

  // Get current units and labels based on active tab
  const currentUnits = activeTab === 'text' ? TEXT_UNITS : IMAGE_UNITS;
  const currentUnitLabels = activeTab === 'text' ? TEXT_UNIT_LABELS : IMAGE_UNIT_LABELS;
  const currentModels = activeTab === 'text' ? pivotedData.textModels : pivotedData.imageModels;

  // Compute filtered providers based on enabled filter
  const filteredProviders = useMemo(() => {
    let models = currentModels;
    if (currentOnly) {
      models = models.filter((m) => enabledModels.has(`${m.provider}::${m.model}`));
    }
    const providerSet = new Set(models.map((m) => m.provider));
    return providers.filter((p) => providerSet.has(p));
  }, [currentModels, currentOnly, enabledModels, providers]);

  // Clear provider filter if it's no longer in the filtered list
  useEffect(() => {
    if (filterProvider && !filteredProviders.includes(filterProvider)) {
      setFilterProvider('');
    }
  }, [filteredProviders, filterProvider]);

  // Filter and sort models
  const filteredAndSortedModels = useMemo(() => {
    let models = [...currentModels];

    // Filter by provider
    if (filterProvider) {
      models = models.filter((m) => m.provider === filterProvider);
    }

    // Filter by search query
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      models = models.filter((m) => m.model.toLowerCase().includes(query));
    }

    // Filter by enabled models
    if (currentOnly) {
      models = models.filter((m) => enabledModels.has(`${m.provider}::${m.model}`));
    }

    // Sort
    models.sort((a, b) => {
      let aVal: string | number | null;
      let bVal: string | number | null;

      if (sortColumn === 'provider') {
        aVal = a.provider;
        bVal = b.provider;
      } else if (sortColumn === 'model') {
        aVal = a.model;
        bVal = b.model;
      } else {
        // Sort by cost column
        aVal = a.costs[sortColumn] ?? null;
        bVal = b.costs[sortColumn] ?? null;
      }

      // Handle nulls - push to end
      if (aVal === null && bVal === null) return 0;
      if (aVal === null) return 1;
      if (bVal === null) return -1;

      // Compare
      if (typeof aVal === 'string' && typeof bVal === 'string') {
        const cmp = aVal.localeCompare(bVal);
        return sortDirection === 'asc' ? cmp : -cmp;
      }

      if (typeof aVal === 'number' && typeof bVal === 'number') {
        const cmp = aVal - bVal;
        return sortDirection === 'asc' ? cmp : -cmp;
      }

      return 0;
    });

    return models;
  }, [currentModels, filterProvider, searchQuery, sortColumn, sortDirection, currentOnly, enabledModels]);

  // Handle column sort click
  const handleSortClick = (column: string) => {
    if (sortColumn === column) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortColumn(column);
      setSortDirection('asc');
    }
  };

  // Handle row click
  const handleRowClick = (model: PivotedModel) => {
    setSelectedModel(model);
  };

  // Keyboard navigation - up/down arrows to navigate models when panel is open
  useEffect(() => {
    if (!selectedModel) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault();

        const currentIndex = filteredAndSortedModels.findIndex(
          (m) => m.provider === selectedModel.provider && m.model === selectedModel.model
        );

        if (currentIndex === -1) return;

        let newIndex: number;
        if (e.key === 'ArrowUp') {
          newIndex = currentIndex > 0 ? currentIndex - 1 : filteredAndSortedModels.length - 1;
        } else {
          newIndex = currentIndex < filteredAndSortedModels.length - 1 ? currentIndex + 1 : 0;
        }

        const targetModel = filteredAndSortedModels[newIndex];

        // Auto-store changes in pending if dirty, then navigate
        const isDirty = slideOutRef.current?.isDirty();
        if (isDirty) {
          const currentValues = slideOutRef.current?.getValues();
          if (currentValues) {
            const key = `${selectedModel.provider}::${selectedModel.model}`;
            setPendingChanges((prev) => {
              const next = new Map(prev);
              next.set(key, {
                provider: selectedModel.provider,
                model: selectedModel.model,
                values: currentValues.values,
                validFrom: currentValues.validFrom,
              });
              return next;
            });
          }
        }

        setSelectedModel(targetModel);
      } else if (e.key === 'Escape') {
        // Auto-store changes before closing
        const isDirty = slideOutRef.current?.isDirty();
        if (isDirty) {
          const currentValues = slideOutRef.current?.getValues();
          if (currentValues) {
            const key = `${selectedModel.provider}::${selectedModel.model}`;
            setPendingChanges((prev) => {
              const next = new Map(prev);
              next.set(key, {
                provider: selectedModel.provider,
                model: selectedModel.model,
                values: currentValues.values,
                validFrom: currentValues.validFrom,
              });
              return next;
            });
          }
        }
        setSelectedModel(null);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedModel, filteredAndSortedModels]);

  // Handle save from slide-out
  const handleSaveModel = async (editValues: Record<string, string>, validFrom: string) => {
    if (!selectedModel) return;

    setSaving(true);
    try {
      const promises: Promise<Response>[] = [];

      for (const unit of currentUnits) {
        const newValue = editValues[unit];
        const originalEntry = selectedModel.originalEntries[unit];
        const originalCost = selectedModel.costs[unit];

        const hasOriginal = originalCost !== null && originalCost !== undefined;
        const hasNew = newValue !== '' && newValue !== undefined;

        if (hasNew) {
          // POST new entry (backend auto-expires old entries for same SKU)
          promises.push(
            adminAPI.fetch('/admin/pricing', {
              method: 'POST',
              body: JSON.stringify({
                provider: selectedModel.provider,
                model: selectedModel.model,
                unit,
                cost: parseFloat(newValue),
                valid_from: validFrom,
              }),
            })
          );
        } else if (hasOriginal && !hasNew) {
          // DELETE old entry (clear the value)
          promises.push(
            adminAPI.fetch(`/admin/pricing/${originalEntry.id}`, {
              method: 'DELETE',
            })
          );
        }
      }

      await Promise.all(promises);

      // Clear any pending changes for this model
      const key = `${selectedModel.provider}::${selectedModel.model}`;
      setPendingChanges((prev) => {
        const next = new Map(prev);
        next.delete(key);
        return next;
      });

      setAlert({ type: 'success', message: 'Pricing updated successfully' });
      setSelectedModel(null); // Close slide-out directly (bypasses dirty check)
      fetchPricing();
    } catch {
      setAlert({ type: 'error', message: 'Failed to save pricing changes' });
    } finally {
      setSaving(false);
    }
  };

  // Handle "Save All" - commit all pending changes to API
  const handleSaveAll = async () => {
    if (pendingChanges.size === 0) return;

    setSaving(true);
    try {
      const promises: Promise<Response>[] = [];

      for (const pending of pendingChanges.values()) {
        for (const unit of currentUnits) {
          const newValue = pending.values[unit];
          if (newValue !== '' && newValue !== undefined) {
            promises.push(
              adminAPI.fetch('/admin/pricing', {
                method: 'POST',
                body: JSON.stringify({
                  provider: pending.provider,
                  model: pending.model,
                  unit,
                  cost: parseFloat(newValue),
                  valid_from: pending.validFrom,
                }),
              })
            );
          }
        }
      }

      await Promise.all(promises);

      setPendingChanges(new Map());
      setAlert({ type: 'success', message: `Saved ${pendingChanges.size} model(s) successfully` });
      fetchPricing();
    } catch {
      setAlert({ type: 'error', message: 'Failed to save pending changes' });
    } finally {
      setSaving(false);
    }
  };

  // Get pending values for current selected model
  const selectedModelPendingValues = useMemo(() => {
    if (!selectedModel) return null;
    const key = `${selectedModel.provider}::${selectedModel.model}`;
    const pending = pendingChanges.get(key);
    if (!pending) return null;
    return { values: pending.values, validFrom: pending.validFrom };
  }, [selectedModel, pendingChanges]);

  // Handle closing the slide-out - auto-store changes in pending
  const handleCloseSlideOut = useCallback(() => {
    if (selectedModel) {
      const isDirty = slideOutRef.current?.isDirty();
      if (isDirty) {
        const currentValues = slideOutRef.current?.getValues();
        if (currentValues) {
          const key = `${selectedModel.provider}::${selectedModel.model}`;
          setPendingChanges((prev) => {
            const next = new Map(prev);
            next.set(key, {
              provider: selectedModel.provider,
              model: selectedModel.model,
              values: currentValues.values,
              validFrom: currentValues.validFrom,
            });
            return next;
          });
        }
      }
    }
    setSelectedModel(null);
  }, [selectedModel]);

  // Add new pricing entry
  const handleAddPricing = async () => {
    if (!newPricing.provider || !newPricing.model || !newPricing.unit || !newPricing.cost) {
      setAlert({ type: 'error', message: 'Please fill in all required fields' });
      return;
    }

    try {
      const response = await adminAPI.fetch('/admin/pricing', {
        method: 'POST',
        body: JSON.stringify({
          provider: newPricing.provider,
          model: newPricing.model,
          unit: newPricing.unit,
          cost: parseFloat(newPricing.cost),
          notes: newPricing.notes || undefined,
        }),
      });

      const data = await response.json();

      if (data.success) {
        setAlert({ type: 'success', message: 'Pricing entry added' });
        setShowAddModal(false);
        setNewPricing({ provider: '', model: '', unit: 'input_tokens_1m', cost: '', notes: '' });
        fetchPricing();
        fetchProviders();
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to add pricing' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  // Clear alert after timeout
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  // Sort indicator
  const SortIndicator = ({ column }: { column: string }) => {
    if (sortColumn !== column) return null;
    return <span className="prm-sort-indicator">{sortDirection === 'asc' ? '▲' : '▼'}</span>;
  };

  if (loading && entries.length === 0) {
    return (
      <div className="prm-loading">
        <div className="prm-loading__spinner" />
        <span>Loading pricing...</span>
      </div>
    );
  }

  return (
    <div className={`prm-container ${embedded ? 'prm-container--embedded' : ''}`}>
      {/* Alert Toast */}
      {alert && (
        <div className={`prm-alert prm-alert--${alert.type}`}>
          <span className="prm-alert__icon">
            {alert.type === 'success' ? '✓' : alert.type === 'error' ? '✕' : 'i'}
          </span>
          <span className="prm-alert__message">{alert.message}</span>
          <button className="prm-alert__close" onClick={() => setAlert(null)}>
            ×
          </button>
        </div>
      )}

      {/* Tabs + Buttons */}
      <div className="prm-tabs">
        <button
          className={`prm-tabs__tab ${activeTab === 'text' ? 'prm-tabs__tab--active' : ''}`}
          onClick={() => setActiveTab('text')}
        >
          Text Models
          <span className="prm-tabs__count">{pivotedData.textModels.length}</span>
        </button>
        <button
          className={`prm-tabs__tab ${activeTab === 'image' ? 'prm-tabs__tab--active' : ''}`}
          onClick={() => setActiveTab('image')}
        >
          Image Models
          <span className="prm-tabs__count">{pivotedData.imageModels.length}</span>
        </button>
        <div className="prm-tabs__actions">
          {pendingChanges.size > 0 && (
            <button
              className="prm-btn prm-btn--save-all"
              onClick={handleSaveAll}
              disabled={saving}
            >
              {saving ? 'Saving...' : `Save All (${pendingChanges.size})`}
            </button>
          )}
          <button className="prm-btn prm-btn--primary" onClick={() => setShowAddModal(true)}>
            + Add Entry
          </button>
        </div>
      </div>

      {/* Filters */}
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

      {/* Pivot Table */}
      <div className="prm-table-wrapper">
        <table className="prm-table prm-table--clickable">
          <thead>
            <tr>
              <th
                className="prm-table__th--sortable"
                onClick={() => handleSortClick('provider')}
              >
                Provider <SortIndicator column="provider" />
              </th>
              <th
                className="prm-table__th--sortable"
                onClick={() => handleSortClick('model')}
              >
                Model <SortIndicator column="model" />
              </th>
              {currentUnits.map((unit) => (
                <th
                  key={unit}
                  className="prm-table__th--sortable prm-table__th--cost"
                  onClick={() => handleSortClick(unit)}
                >
                  {(currentUnitLabels as Record<string, string>)[unit]} <SortIndicator column={unit} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredAndSortedModels.length === 0 ? (
              <tr>
                <td colSpan={2 + currentUnits.length} className="prm-table__empty">
                  No {activeTab} models found
                </td>
              </tr>
            ) : (
              filteredAndSortedModels.map((model) => {
                const isSelected =
                  selectedModel?.provider === model.provider &&
                  selectedModel?.model === model.model;
                const modelKey = `${model.provider}::${model.model}`;
                const hasPending = pendingChanges.has(modelKey);
                return (
                  <tr
                    key={modelKey}
                    className={`prm-table__row--clickable ${
                      isSelected ? 'prm-table__row--selected' : ''
                    } ${hasPending ? 'prm-table__row--pending' : ''}`}
                    onClick={() => handleRowClick(model)}
                  >
                    <td>{model.provider}</td>
                    <td className="prm-table__model">{model.model}</td>
                    {currentUnits.map((unit) => (
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

      {/* Slide-Out Panel */}
      {selectedModel && (
        <PricingSlideOut
          ref={slideOutRef}
          model={selectedModel}
          units={currentUnits}
          unitLabels={currentUnitLabels}
          onClose={handleCloseSlideOut}
          onSave={handleSaveModel}
          saving={saving}
          pendingValues={selectedModelPendingValues}
        />
      )}

      {/* Add Modal */}
      {showAddModal && (
        <div className="prm-modal-overlay" onClick={() => setShowAddModal(false)}>
          <div className="prm-modal" onClick={(e) => e.stopPropagation()}>
            <h3 className="prm-modal__title">Add Pricing Entry</h3>

            <div className="prm-form">
              <div className="prm-form__group">
                <label>Provider *</label>
                <input
                  type="text"
                  className="prm-input"
                  value={newPricing.provider}
                  onChange={(e) => setNewPricing((p) => ({ ...p, provider: e.target.value }))}
                  placeholder="e.g., openai"
                  list="providers-list"
                />
                <datalist id="providers-list">
                  {providers.map((p) => (
                    <option key={p} value={p} />
                  ))}
                </datalist>
              </div>

              <div className="prm-form__group">
                <label>Model *</label>
                <input
                  type="text"
                  className="prm-input"
                  value={newPricing.model}
                  onChange={(e) => setNewPricing((p) => ({ ...p, model: e.target.value }))}
                  placeholder="e.g., gpt-4o"
                />
              </div>

              <div className="prm-form__group">
                <label>Unit *</label>
                <select
                  className="prm-select"
                  value={newPricing.unit}
                  onChange={(e) => setNewPricing((p) => ({ ...p, unit: e.target.value }))}
                >
                  <optgroup label="Text Models">
                    {TEXT_UNITS.map((unit) => (
                      <option key={unit} value={unit}>
                        {TEXT_UNIT_LABELS[unit]}
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label="Image Models">
                    {IMAGE_UNITS.map((unit) => (
                      <option key={unit} value={unit}>
                        {IMAGE_UNIT_LABELS[unit]}
                      </option>
                    ))}
                  </optgroup>
                </select>
              </div>

              <div className="prm-form__group">
                <label>Cost (USD) *</label>
                <input
                  type="number"
                  className="prm-input"
                  value={newPricing.cost}
                  onChange={(e) => setNewPricing((p) => ({ ...p, cost: e.target.value }))}
                  placeholder="e.g., 2.50"
                  step="0.001"
                  min="0"
                />
              </div>

              <div className="prm-form__group">
                <label>Notes</label>
                <input
                  type="text"
                  className="prm-input"
                  value={newPricing.notes}
                  onChange={(e) => setNewPricing((p) => ({ ...p, notes: e.target.value }))}
                  placeholder="Optional notes"
                />
              </div>
            </div>

            <div className="prm-modal__actions">
              <button className="prm-btn prm-btn--ghost" onClick={() => setShowAddModal(false)}>
                Cancel
              </button>
              <button className="prm-btn prm-btn--primary" onClick={handleAddPricing}>
                Add Entry
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default PricingManager;
