import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { adminAPI } from '../../../utils/api';
import { logger } from '../../../utils/logger';
import { useViewport } from '../../../hooks/useViewport';
import type {
  PricingEntry,
  AlertState,
  PricingManagerProps,
  NewPricing,
  PivotedModel,
  TabType,
  SortDirection,
  PendingChange,
  SlideOutRef,
} from './types';
import {
  TEXT_UNITS,
  IMAGE_UNITS,
  TEXT_UNIT_LABELS,
  IMAGE_UNIT_LABELS,
  pivotPricingData,
} from './pricingUtils';
import { PricingSlideOut } from './PricingSlideOut';
import { PricingTable } from './PricingTable';
import { PricingFilters } from './PricingFilters';
import { AddPricingModal } from './AddPricingModal';
import './PricingManager.css';

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

  // Pivot table state
  const [activeTab, setActiveTab] = useState<TabType>('text');
  const [selectedModel, setSelectedModel] = useState<PivotedModel | null>(null);
  const [saving, setSaving] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [sortColumn, setSortColumn] = useState<string>('provider');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  // Pending changes (unsaved edits stashed across row navigation)
  const [pendingChanges, setPendingChanges] = useState<Map<string, PendingChange>>(new Map());
  const slideOutRef = useRef<SlideOutRef>(null);

  // Mobile filter sheet
  const { isMobile } = useViewport();
  const [filterSheetOpen, setFilterSheetOpen] = useState(false);
  const activeFilterCount =
    (filterProvider ? 1 : 0) + (searchQuery ? 1 : 0) + (!currentOnly ? 1 : 0);

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

  // Current units and labels based on active tab
  const currentUnits = activeTab === 'text' ? TEXT_UNITS : IMAGE_UNITS;
  const currentUnitLabels = (activeTab === 'text' ? TEXT_UNIT_LABELS : IMAGE_UNIT_LABELS) as Record<
    string,
    string
  >;
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

    if (filterProvider) {
      models = models.filter((m) => m.provider === filterProvider);
    }

    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      models = models.filter((m) => m.model.toLowerCase().includes(query));
    }

    if (currentOnly) {
      models = models.filter((m) => enabledModels.has(`${m.provider}::${m.model}`));
    }

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
        aVal = a.costs[sortColumn] ?? null;
        bVal = b.costs[sortColumn] ?? null;
      }

      // Handle nulls - push to end
      if (aVal === null && bVal === null) return 0;
      if (aVal === null) return 1;
      if (bVal === null) return -1;

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
  }, [
    currentModels,
    filterProvider,
    searchQuery,
    sortColumn,
    sortDirection,
    currentOnly,
    enabledModels,
  ]);

  // Handle column sort click
  const handleSortClick = (column: string) => {
    if (sortColumn === column) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortColumn(column);
      setSortDirection('asc');
    }
  };

  const handleRowClick = (model: PivotedModel) => {
    setSelectedModel(model);
  };

  // Stash the slide-out's dirty edits into pendingChanges for a given model.
  const stashDirty = useCallback((model: PivotedModel) => {
    if (!slideOutRef.current?.isDirty()) return;
    const currentValues = slideOutRef.current?.getValues();
    if (!currentValues) return;
    const key = `${model.provider}::${model.model}`;
    setPendingChanges((prev) => {
      const next = new Map(prev);
      next.set(key, {
        provider: model.provider,
        model: model.model,
        values: currentValues.values,
        validFrom: currentValues.validFrom,
      });
      return next;
    });
  }, []);

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
        stashDirty(selectedModel);
        setSelectedModel(targetModel);
      } else if (e.key === 'Escape') {
        // Auto-store changes before closing
        stashDirty(selectedModel);
        setSelectedModel(null);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedModel, filteredAndSortedModels, stashDirty]);

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

  // Pending values for the currently-selected model
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
      stashDirty(selectedModel);
    }
    setSelectedModel(null);
  }, [selectedModel, stashDirty]);

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
              className="admin-btn prm-btn--save-all"
              onClick={handleSaveAll}
              disabled={saving}
            >
              {saving ? 'Saving...' : `Save All (${pendingChanges.size})`}
            </button>
          )}
          {!isMobile && (
            <button className="admin-btn admin-btn--primary" onClick={() => setShowAddModal(true)}>
              + Add Entry
            </button>
          )}
        </div>
      </div>

      {/* Filters - mobile uses filter button + bottom sheet */}
      <PricingFilters
        isMobile={isMobile}
        filterProvider={filterProvider}
        setFilterProvider={setFilterProvider}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        currentOnly={currentOnly}
        setCurrentOnly={setCurrentOnly}
        filteredProviders={filteredProviders}
        filterSheetOpen={filterSheetOpen}
        setFilterSheetOpen={setFilterSheetOpen}
        activeFilterCount={activeFilterCount}
        onAddEntry={() => setShowAddModal(true)}
      />

      {/* Pivot Table */}
      <PricingTable
        models={filteredAndSortedModels}
        units={currentUnits}
        unitLabels={currentUnitLabels}
        sortColumn={sortColumn}
        sortDirection={sortDirection}
        onSort={handleSortClick}
        selectedModel={selectedModel}
        pendingChanges={pendingChanges}
        onRowClick={handleRowClick}
        activeTab={activeTab}
      />

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
        <AddPricingModal
          newPricing={newPricing}
          setNewPricing={setNewPricing}
          providers={providers}
          onCancel={() => setShowAddModal(false)}
          onAdd={handleAddPricing}
        />
      )}
    </div>
  );
}

export default PricingManager;
