import { useState, useEffect, useCallback, useMemo } from 'react';
import { Sliders, Database, HardDrive, DollarSign } from 'lucide-react';
import { config } from '../../config';
import { useAdminResource, useAdminMutation } from '../../hooks/useAdminResource';
import './AdminShared.css';
import './UnifiedSettings.css';

// ============================================
// Types
// ============================================

type SettingsCategory = 'models' | 'capture' | 'storage' | 'pricing';

interface CategoryConfig {
  id: SettingsCategory;
  label: string;
  description: string;
  icon: React.ReactNode;
}

// Model types
interface Model {
  id: number;
  provider: string;
  model: string;
  enabled: boolean;
  display_name: string | null;
  notes: string | null;
  supports_reasoning: boolean;
  supports_json_mode: boolean;
  supports_image_gen: boolean;
  sort_order: number;
  updated_at: string;
}

// Settings types
interface SettingConfig {
  value: string;
  options?: string[];
  type?: string;
  description: string;
  env_default: string;
  is_db_override: boolean;
}

interface CaptureSettingsData {
  LLM_PROMPT_CAPTURE: SettingConfig;
  LLM_PROMPT_RETENTION_DAYS: SettingConfig;
}

interface CaptureStats {
  total: number;
  by_call_type?: Record<string, number>;
  by_provider?: Record<string, number>;
}

// Storage types
interface CategoryStats {
  rows: number;
  bytes: number;
  percentage: number;
}

interface StorageStats {
  total_bytes: number;
  total_mb: number;
  categories: Record<string, CategoryStats>;
  tables: Record<string, { rows: number; bytes: number }>;
}

// Pricing types
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

interface UnifiedSettingsProps {
  embedded?: boolean;
}

// ============================================
// Category Configuration
// ============================================

const CATEGORIES: CategoryConfig[] = [
  {
    id: 'models',
    label: 'Models',
    description: 'Enable or disable LLM models',
    icon: <Sliders size={20} />,
  },
  {
    id: 'capture',
    label: 'Capture',
    description: 'Prompt capture settings',
    icon: <Database size={20} />,
  },
  {
    id: 'storage',
    label: 'Storage',
    description: 'Database size breakdown',
    icon: <HardDrive size={20} />,
  },
  {
    id: 'pricing',
    label: 'Pricing',
    description: 'Model pricing config',
    icon: <DollarSign size={20} />,
  },
];

// ============================================
// Main Component
// ============================================

export function UnifiedSettings({ embedded = false }: UnifiedSettingsProps) {
  const [activeCategory, setActiveCategory] = useState<SettingsCategory>('models');
  const [alert, setAlert] = useState<AlertState | null>(null);

  // Models state - using hook
  const {
    data: models,
    loading: modelsLoading,
    refresh: refreshModels
  } = useAdminResource<Model[]>('/admin/api/models', {
    transform: (result) => (result as { models: Model[] }).models,
    onError: (err) => showAlert('error', err),
  });
  const [expandedProviders, setExpandedProviders] = useState<Set<string>>(new Set());

  // Capture state
  const [captureSettings, setCaptureSettings] = useState<CaptureSettingsData | null>(null);
  const [captureStats, setCaptureStats] = useState<CaptureStats | null>(null);
  const [captureLoading, setCaptureLoading] = useState(true);
  const [editedCapture, setEditedCapture] = useState<string>('');
  const [editedRetention, setEditedRetention] = useState<string>('');
  const [captureSaving, setCaptureSaving] = useState(false);

  // Storage state
  const [storage, setStorage] = useState<StorageStats | null>(null);
  const [storageLoading, setStorageLoading] = useState(true);

  // Pricing state
  const [pricing, setPricing] = useState<PricingEntry[]>([]);
  const [pricingLoading, setPricingLoading] = useState(true);
  const [pricingProviders, setPricingProviders] = useState<string[]>([]);
  const [filterProvider, setFilterProvider] = useState('');
  const [currentOnly, setCurrentOnly] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null);
  const [newPricing, setNewPricing] = useState({
    provider: '',
    model: '',
    unit: 'input_tokens_1m',
    cost: '',
    notes: '',
  });

  // Auto-dismiss alerts
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 4000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  const showAlert = (type: AlertState['type'], message: string) => {
    setAlert({ type, message });
  };

  // ============================================
  // Models Logic
  // ============================================

  const fetchModels = useCallback(async () => {
    try {
      setModelsLoading(true);
      const response = await fetch(`${config.API_URL}/admin/api/models`);
      const data = await response.json();

      if (data.success) {
        setModels(data.models);
        const providers = new Set<string>(data.models.map((m: Model) => m.provider));
        setExpandedProviders(providers);
      } else {
        showAlert('error', data.error || 'Failed to load models');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setModelsLoading(false);
    }
  }, []);

  const toggleModel = async (modelId: number, enabled: boolean) => {
    try {
      const response = await fetch(`${config.API_URL}/admin/api/models/${modelId}/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });

      const data = await response.json();

      if (data.success) {
        setModels(prev => prev.map(m =>
          m.id === modelId ? { ...m, enabled } : m
        ));
        showAlert('success', `Model ${enabled ? 'enabled' : 'disabled'}`);
      } else {
        showAlert('error', data.error || 'Failed to update model');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    }
  };

  const toggleProvider = (provider: string) => {
    setExpandedProviders(prev => {
      const next = new Set(prev);
      if (next.has(provider)) {
        next.delete(provider);
      } else {
        next.add(provider);
      }
      return next;
    });
  };

  const modelsByProvider = useMemo(() => {
    return models.reduce((acc, model) => {
      if (!acc[model.provider]) {
        acc[model.provider] = [];
      }
      acc[model.provider].push(model);
      return acc;
    }, {} as Record<string, Model[]>);
  }, [models]);

  // ============================================
  // Capture Logic
  // ============================================

  const fetchCaptureData = useCallback(async () => {
    try {
      setCaptureLoading(true);
      const [settingsRes, statsRes] = await Promise.all([
        fetch(`${config.API_URL}/admin/api/settings`),
        fetch(`${config.API_URL}/admin/api/playground/stats`),
      ]);

      const settingsData = await settingsRes.json();
      const statsData = await statsRes.json();

      if (settingsData.success) {
        setCaptureSettings(settingsData.settings);
        setEditedCapture(settingsData.settings.LLM_PROMPT_CAPTURE.value);
        setEditedRetention(settingsData.settings.LLM_PROMPT_RETENTION_DAYS.value);
      } else {
        showAlert('error', settingsData.error || 'Failed to load settings');
      }

      if (statsData.success) {
        setCaptureStats(statsData.stats);
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setCaptureLoading(false);
    }
  }, []);

  const saveCaptureSettings = async () => {
    if (!captureSettings) return;

    setCaptureSaving(true);
    try {
      const updates: Promise<Response>[] = [];

      if (editedCapture !== captureSettings.LLM_PROMPT_CAPTURE.value) {
        updates.push(fetch(`${config.API_URL}/admin/api/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'LLM_PROMPT_CAPTURE', value: editedCapture }),
        }));
      }

      if (editedRetention !== captureSettings.LLM_PROMPT_RETENTION_DAYS.value) {
        updates.push(fetch(`${config.API_URL}/admin/api/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'LLM_PROMPT_RETENTION_DAYS', value: editedRetention }),
        }));
      }

      await Promise.all(updates);
      showAlert('success', 'Settings saved');

      // Update local state
      setCaptureSettings(prev => prev ? {
        ...prev,
        LLM_PROMPT_CAPTURE: { ...prev.LLM_PROMPT_CAPTURE, value: editedCapture, is_db_override: true },
        LLM_PROMPT_RETENTION_DAYS: { ...prev.LLM_PROMPT_RETENTION_DAYS, value: editedRetention, is_db_override: true },
      } : null);
    } catch {
      showAlert('error', 'Failed to save settings');
    } finally {
      setCaptureSaving(false);
    }
  };

  const resetCaptureSettings = async () => {
    setCaptureSaving(true);
    try {
      const response = await fetch(`${config.API_URL}/admin/api/settings/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });

      const data = await response.json();

      if (data.success) {
        showAlert('success', 'Settings reset to defaults');
        await fetchCaptureData();
      } else {
        showAlert('error', data.error || 'Failed to reset settings');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setCaptureSaving(false);
    }
  };

  const hasCaptureChanges = captureSettings && (
    editedCapture !== captureSettings.LLM_PROMPT_CAPTURE.value ||
    editedRetention !== captureSettings.LLM_PROMPT_RETENTION_DAYS.value
  );

  // ============================================
  // Storage Logic
  // ============================================

  const fetchStorage = useCallback(async () => {
    try {
      setStorageLoading(true);
      const response = await fetch(`${config.API_URL}/admin/api/settings/storage`);
      const data = await response.json();

      if (data.success) {
        setStorage(data.storage);
      } else {
        showAlert('error', data.error || 'Failed to load storage stats');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setStorageLoading(false);
    }
  }, []);

  // ============================================
  // Pricing Logic
  // ============================================

  const fetchPricing = useCallback(async () => {
    try {
      setPricingLoading(true);
      const params = new URLSearchParams();
      if (filterProvider) params.append('provider', filterProvider);
      if (currentOnly) params.append('current_only', 'true');

      const response = await fetch(`${config.API_URL}/admin/pricing?${params}`);
      const data = await response.json();

      if (data.success) {
        setPricing(data.pricing || []);
      } else {
        showAlert('error', data.error || 'Failed to load pricing');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setPricingLoading(false);
    }
  }, [filterProvider, currentOnly]);

  const fetchPricingProviders = useCallback(async () => {
    try {
      const response = await fetch(`${config.API_URL}/admin/pricing/providers`);
      const data = await response.json();
      if (data.success) {
        setPricingProviders(data.providers.map((p: { provider: string }) => p.provider));
      }
    } catch (error) {
      console.error('Failed to fetch providers:', error);
    }
  }, []);

  const handleAddPricing = async () => {
    if (!newPricing.provider || !newPricing.model || !newPricing.unit || !newPricing.cost) {
      showAlert('error', 'Please fill in all required fields');
      return;
    }

    try {
      const response = await fetch(`${config.API_URL}/admin/pricing`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
        showAlert('success', 'Pricing entry added');
        setShowAddModal(false);
        setNewPricing({ provider: '', model: '', unit: 'input_tokens_1m', cost: '', notes: '' });
        fetchPricing();
        fetchPricingProviders();
      } else {
        showAlert('error', data.error || 'Failed to add pricing');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    }
  };

  const handleDeletePricing = async (id: number) => {
    try {
      const response = await fetch(`${config.API_URL}/admin/pricing/${id}`, {
        method: 'DELETE',
      });

      const data = await response.json();

      if (data.success) {
        showAlert('success', 'Pricing entry deleted');
        setDeleteConfirm(null);
        fetchPricing();
      } else {
        showAlert('error', data.error || 'Failed to delete pricing');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    }
  };

  // Load data when category changes
  useEffect(() => {
    if (activeCategory === 'models' && models.length === 0) {
      fetchModels();
    } else if (activeCategory === 'capture' && !captureSettings) {
      fetchCaptureData();
    } else if (activeCategory === 'storage' && !storage) {
      fetchStorage();
    } else if (activeCategory === 'pricing' && pricing.length === 0) {
      fetchPricing();
      fetchPricingProviders();
    }
  }, [activeCategory, models.length, captureSettings, storage, pricing.length, fetchModels, fetchCaptureData, fetchStorage, fetchPricing, fetchPricingProviders]);

  // Refetch pricing when filters change
  useEffect(() => {
    if (activeCategory === 'pricing') {
      fetchPricing();
    }
  }, [filterProvider, currentOnly, activeCategory, fetchPricing]);

  const activeCategoryConfig = CATEGORIES.find(c => c.id === activeCategory);

  // ============================================
  // Render Sections
  // ============================================

  const renderModelsSection = () => {
    if (modelsLoading) {
      return (
        <div className="admin-loading">
          <div className="admin-loading__spinner" />
          <span className="admin-loading__text">Loading models...</span>
        </div>
      );
    }

    return (
      <div className="us-models">
        {Object.entries(modelsByProvider).map(([provider, providerModels]) => (
          <div key={provider} className="us-provider">
            <button
              className="us-provider__header"
              onClick={() => toggleProvider(provider)}
              type="button"
            >
              <span className={`us-provider__chevron ${expandedProviders.has(provider) ? 'us-provider__chevron--open' : ''}`}>
                <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                  <path d="M7.5 5L12.5 10L7.5 15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </span>
              <span className="us-provider__name">{provider}</span>
              <span className="us-provider__count">
                {providerModels.filter(m => m.enabled).length}/{providerModels.length} enabled
              </span>
            </button>

            {expandedProviders.has(provider) && (
              <div className="us-provider__models">
                {providerModels.map(model => (
                  <div key={model.id} className={`us-model ${!model.enabled ? 'us-model--disabled' : ''}`}>
                    <div className="us-model__info">
                      <span className="us-model__name">
                        {model.display_name || model.model}
                      </span>
                      <div className="us-model__capabilities">
                        {model.supports_reasoning && (
                          <span className="us-cap us-cap--reasoning" title="Supports reasoning">R</span>
                        )}
                        {model.supports_json_mode && (
                          <span className="us-cap us-cap--json" title="Supports JSON mode">J</span>
                        )}
                        {model.supports_image_gen && (
                          <span className="us-cap us-cap--image" title="Supports image generation">I</span>
                        )}
                      </div>
                    </div>
                    <label className="admin-toggle">
                      <input
                        type="checkbox"
                        className="admin-toggle__input"
                        checked={model.enabled}
                        onChange={(e) => toggleModel(model.id, e.target.checked)}
                      />
                      <span className="admin-toggle__switch">
                        <span className="admin-toggle__slider" />
                      </span>
                    </label>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}

        <div className="us-legend">
          <span className="us-legend__title">Capabilities:</span>
          <span className="us-cap us-cap--reasoning">R</span> Reasoning
          <span className="us-cap us-cap--json">J</span> JSON Mode
          <span className="us-cap us-cap--image">I</span> Image Gen
        </div>
      </div>
    );
  };

  const renderCaptureSection = () => {
    if (captureLoading || !captureSettings) {
      return (
        <div className="admin-loading">
          <div className="admin-loading__spinner" />
          <span className="admin-loading__text">Loading settings...</span>
        </div>
      );
    }

    return (
      <div className="us-capture">
        <div className="admin-card">
          <div className="admin-card__header">
            <h3 className="admin-card__title">Capture Mode</h3>
            {captureSettings.LLM_PROMPT_CAPTURE.is_db_override && (
              <span className="admin-badge admin-badge--primary">Custom</span>
            )}
          </div>
          <p className="admin-card__subtitle">{captureSettings.LLM_PROMPT_CAPTURE.description}</p>
          <div className="us-capture__options">
            {captureSettings.LLM_PROMPT_CAPTURE.options?.map(option => (
              <button
                key={option}
                className={`us-option ${editedCapture === option ? 'us-option--selected' : ''}`}
                onClick={() => setEditedCapture(option)}
                disabled={captureSaving}
                type="button"
              >
                <span className="us-option__label">{formatOptionLabel(option)}</span>
                {option === captureSettings.LLM_PROMPT_CAPTURE.env_default && (
                  <span className="us-option__default">default</span>
                )}
              </button>
            ))}
          </div>
        </div>

        <div className="admin-card">
          <div className="admin-card__header">
            <h3 className="admin-card__title">Retention Period</h3>
            {captureSettings.LLM_PROMPT_RETENTION_DAYS.is_db_override && (
              <span className="admin-badge admin-badge--primary">Custom</span>
            )}
          </div>
          <p className="admin-card__subtitle">{captureSettings.LLM_PROMPT_RETENTION_DAYS.description}</p>
          <div className="us-capture__retention">
            <input
              type="number"
              className="admin-input"
              value={editedRetention}
              onChange={(e) => setEditedRetention(e.target.value)}
              min="0"
              disabled={captureSaving}
              style={{ maxWidth: '120px' }}
            />
            <span className="us-capture__unit">days</span>
            {editedRetention === '0' && (
              <span className="admin-badge admin-badge--warning">Unlimited</span>
            )}
          </div>
        </div>

        {captureStats && (
          <div className="admin-card">
            <h3 className="admin-card__title">Statistics</h3>
            <div className="us-stats">
              <div className="us-stat">
                <span className="us-stat__value">{captureStats.total?.toLocaleString() || 0}</span>
                <span className="us-stat__label">Total Captures</span>
              </div>
            </div>
            {captureStats.by_call_type && Object.keys(captureStats.by_call_type).length > 0 && (
              <div className="us-stat-breakdown">
                <span className="us-stat-breakdown__title">By Call Type</span>
                <div className="us-stat-breakdown__items">
                  {Object.entries(captureStats.by_call_type)
                    .sort(([, a], [, b]) => b - a)
                    .slice(0, 5)
                    .map(([type, count]) => (
                      <div key={type} className="us-stat-item">
                        <span className="us-stat-item__label">{type}</span>
                        <span className="us-stat-item__value">{count.toLocaleString()}</span>
                      </div>
                    ))}
                </div>
              </div>
            )}
          </div>
        )}

        <div className="us-capture__actions">
          <button
            type="button"
            className="admin-btn admin-btn--primary"
            onClick={saveCaptureSettings}
            disabled={captureSaving || !hasCaptureChanges}
          >
            {captureSaving ? 'Saving...' : 'Save Changes'}
          </button>
          <button
            type="button"
            className="admin-btn admin-btn--secondary"
            onClick={resetCaptureSettings}
            disabled={captureSaving}
          >
            Reset to Defaults
          </button>
        </div>
      </div>
    );
  };

  const renderStorageSection = () => {
    if (storageLoading || !storage) {
      return (
        <div className="admin-loading">
          <div className="admin-loading__spinner" />
          <span className="admin-loading__text">Loading storage stats...</span>
        </div>
      );
    }

    return (
      <div className="us-storage">
        <div className="us-storage__total">
          <span className="us-storage__total-value">{storage.total_mb.toFixed(2)}</span>
          <span className="us-storage__total-unit">MB</span>
          <span className="us-storage__total-label">Total Database Size</span>
        </div>

        <div className="us-storage__breakdown">
          {Object.entries(storage.categories)
            .sort(([, a], [, b]) => b.bytes - a.bytes)
            .map(([category, stats]) => (
              <div key={category} className="us-storage__category">
                <div className="us-storage__category-header">
                  <span className="us-storage__category-name">{formatCategoryName(category)}</span>
                  <span className="us-storage__category-size">{formatBytes(stats.bytes)}</span>
                </div>
                <div className="us-storage__bar">
                  <div
                    className={`us-storage__bar-fill us-storage__bar-fill--${category}`}
                    style={{ width: `${Math.max(stats.percentage, 1)}%` }}
                  />
                </div>
                <div className="us-storage__category-meta">
                  <span>{stats.rows.toLocaleString()} rows</span>
                  <span>{stats.percentage.toFixed(1)}%</span>
                </div>
              </div>
            ))}
        </div>

        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={fetchStorage}
        >
          Refresh
        </button>
      </div>
    );
  };

  const renderPricingSection = () => {
    if (pricingLoading && pricing.length === 0) {
      return (
        <div className="admin-loading">
          <div className="admin-loading__spinner" />
          <span className="admin-loading__text">Loading pricing...</span>
        </div>
      );
    }

    return (
      <div className="us-pricing">
        <div className="us-pricing__header">
          <div className="us-pricing__filters">
            <select
              className="admin-input admin-select"
              value={filterProvider}
              onChange={(e) => setFilterProvider(e.target.value)}
            >
              <option value="">All Providers</option>
              {pricingProviders.map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>

            <label className="admin-checkbox">
              <input
                type="checkbox"
                className="admin-checkbox__input"
                checked={currentOnly}
                onChange={(e) => setCurrentOnly(e.target.checked)}
              />
              <span className="admin-checkbox__label">Current only</span>
            </label>
          </div>

          <button
            type="button"
            className="admin-btn admin-btn--primary"
            onClick={() => setShowAddModal(true)}
          >
            + Add Entry
          </button>
        </div>

        <div className="us-pricing__table-wrapper">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Model</th>
                <th>Unit</th>
                <th>Cost</th>
                <th>Valid From</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {pricing.length === 0 ? (
                <tr>
                  <td colSpan={6} className="us-pricing__empty">
                    No pricing entries found
                  </td>
                </tr>
              ) : (
                pricing.map(entry => (
                  <tr key={entry.id} className={entry.valid_until ? 'us-pricing__row--expired' : ''}>
                    <td>{entry.provider}</td>
                    <td className="us-pricing__model">{entry.model}</td>
                    <td>{entry.unit}</td>
                    <td className="us-pricing__cost">{formatCost(entry.cost, entry.unit)}</td>
                    <td>{entry.valid_from ? new Date(entry.valid_from).toLocaleDateString() : '-'}</td>
                    <td>
                      {deleteConfirm === entry.id ? (
                        <div className="us-pricing__confirm">
                          <button
                            type="button"
                            className="admin-btn admin-btn--danger admin-btn--sm"
                            onClick={() => handleDeletePricing(entry.id)}
                          >
                            Confirm
                          </button>
                          <button
                            type="button"
                            className="admin-btn admin-btn--ghost admin-btn--sm"
                            onClick={() => setDeleteConfirm(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="admin-btn admin-btn--ghost admin-btn--sm"
                          onClick={() => setDeleteConfirm(entry.id)}
                        >
                          Delete
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  const renderContent = () => {
    switch (activeCategory) {
      case 'models':
        return renderModelsSection();
      case 'capture':
        return renderCaptureSection();
      case 'storage':
        return renderStorageSection();
      case 'pricing':
        return renderPricingSection();
      default:
        return null;
    }
  };

  return (
    <div className={`admin-master-detail ${embedded ? '' : 'us-standalone'}`}>
      {/* Alert Toast */}
      {alert && (
        <div className="admin-toast-container">
          <div className={`admin-alert admin-alert--${alert.type}`}>
            <span className="admin-alert__icon">
              {alert.type === 'success' && '✓'}
              {alert.type === 'error' && '✕'}
              {alert.type === 'info' && 'i'}
            </span>
            <span className="admin-alert__content">{alert.message}</span>
            <button className="admin-alert__dismiss" onClick={() => setAlert(null)}>×</button>
          </div>
        </div>
      )}

      {/* Master Panel - Category List */}
      <aside className="admin-master admin-master--open">
        <div className="admin-master__header">
          <h3 className="admin-master__title">Settings</h3>
        </div>
        <div className="admin-master__list">
          {CATEGORIES.map(category => (
            <button
              key={category.id}
              type="button"
              className={`admin-master__item ${activeCategory === category.id ? 'admin-master__item--selected' : ''}`}
              onClick={() => setActiveCategory(category.id)}
            >
              <span className="admin-master__item-icon">{category.icon}</span>
              <span className="admin-master__item-name">{category.label}</span>
            </button>
          ))}
        </div>
      </aside>

      {/* Detail Panel */}
      <main className="admin-detail">
        <div className="admin-detail__header">
          <div>
            <h2 className="admin-detail__title">{activeCategoryConfig?.label}</h2>
            <p className="admin-detail__subtitle">{activeCategoryConfig?.description}</p>
          </div>
        </div>

        <div className="admin-detail__content">
          {renderContent()}
        </div>
      </main>

      {/* Add Pricing Modal */}
      {showAddModal && (
        <div className="admin-modal-overlay" onClick={() => setShowAddModal(false)}>
          <div className="admin-modal" onClick={(e) => e.stopPropagation()}>
            <div className="admin-modal__header">
              <h3 className="admin-modal__title">Add Pricing Entry</h3>
              <button className="admin-modal__close" onClick={() => setShowAddModal(false)}>×</button>
            </div>

            <div className="admin-modal__body">
              <div className="admin-form-group">
                <label className="admin-label">Provider *</label>
                <input
                  type="text"
                  className="admin-input"
                  value={newPricing.provider}
                  onChange={(e) => setNewPricing(p => ({ ...p, provider: e.target.value }))}
                  placeholder="e.g., openai"
                  list="providers-list"
                />
                <datalist id="providers-list">
                  {pricingProviders.map(p => (
                    <option key={p} value={p} />
                  ))}
                </datalist>
              </div>

              <div className="admin-form-group">
                <label className="admin-label">Model *</label>
                <input
                  type="text"
                  className="admin-input"
                  value={newPricing.model}
                  onChange={(e) => setNewPricing(p => ({ ...p, model: e.target.value }))}
                  placeholder="e.g., gpt-4o"
                />
              </div>

              <div className="admin-form-group">
                <label className="admin-label">Unit *</label>
                <select
                  className="admin-input admin-select"
                  value={newPricing.unit}
                  onChange={(e) => setNewPricing(p => ({ ...p, unit: e.target.value }))}
                >
                  <option value="input_tokens_1m">Input Tokens (per 1M)</option>
                  <option value="output_tokens_1m">Output Tokens (per 1M)</option>
                  <option value="cached_input_tokens_1m">Cached Input Tokens (per 1M)</option>
                  <option value="reasoning_tokens_1m">Reasoning Tokens (per 1M)</option>
                  <option value="image_1024x1024">Image (1024x1024)</option>
                  <option value="image_512x512">Image (512x512)</option>
                </select>
              </div>

              <div className="admin-form-group">
                <label className="admin-label">Cost (USD) *</label>
                <input
                  type="number"
                  className="admin-input"
                  value={newPricing.cost}
                  onChange={(e) => setNewPricing(p => ({ ...p, cost: e.target.value }))}
                  placeholder="e.g., 2.50"
                  step="0.001"
                  min="0"
                />
              </div>

              <div className="admin-form-group">
                <label className="admin-label">Notes</label>
                <input
                  type="text"
                  className="admin-input"
                  value={newPricing.notes}
                  onChange={(e) => setNewPricing(p => ({ ...p, notes: e.target.value }))}
                  placeholder="Optional notes"
                />
              </div>
            </div>

            <div className="admin-modal__footer">
              <button
                type="button"
                className="admin-btn admin-btn--secondary"
                onClick={() => setShowAddModal(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="admin-btn admin-btn--primary"
                onClick={handleAddPricing}
              >
                Add Entry
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================
// Helper Functions
// ============================================

function formatOptionLabel(option: string): string {
  switch (option) {
    case 'disabled':
      return 'Disabled';
    case 'all':
      return 'Capture All';
    case 'all_except_decisions':
      return 'All Except Decisions';
    default:
      return option;
  }
}

function formatCategoryName(category: string): string {
  const names: Record<string, string> = {
    captures: 'Prompt Captures',
    api_usage: 'API Usage Logs',
    game_data: 'Game Data',
    ai_state: 'AI State',
    config: 'Configuration',
    assets: 'Avatar Images',
    other: 'Other',
  };
  return names[category] || category;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i > 1 ? 2 : 0)} ${sizes[i]}`;
}

function formatCost(cost: number, unit: string) {
  const isPerMillion = unit.includes('_1m');
  if (isPerMillion) {
    return `$${cost.toFixed(2)} / 1M`;
  }
  return `$${cost.toFixed(4)}`;
}

export default UnifiedSettings;
