import { useState, useEffect, useCallback, useMemo } from 'react';
import { Sliders, Database, HardDrive, DollarSign, Menu, Check, Palette } from 'lucide-react';
import { adminFetch } from '../../utils/api';
import { useAdminResource } from '../../hooks/useAdminResource';
import { useViewport } from '../../hooks/useViewport';
import { useDeckPack } from '../../hooks/useDeckPack';
import { DECK_PACKS } from '../../hooks/deckPacks';
import { getCardImagePathForPack } from '../../utils/cards';
import { MobileFilterSheet } from './shared/MobileFilterSheet';
import { PricingManager } from './PricingManager';
import './AdminShared.css';
import './UnifiedSettings.css';

// ============================================
// Types
// ============================================

export type SettingsCategory = 'models' | 'capture' | 'storage' | 'pricing' | 'appearance';

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
  user_enabled: boolean;
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

// System settings include model configurations
interface SystemSettingsData {
  DEFAULT_PROVIDER: SettingConfig;
  DEFAULT_MODEL: SettingConfig;
  FAST_PROVIDER: SettingConfig;
  FAST_MODEL: SettingConfig;
  IMAGE_PROVIDER: SettingConfig;
  IMAGE_MODEL: SettingConfig;
  ASSISTANT_PROVIDER: SettingConfig;
  ASSISTANT_MODEL: SettingConfig;
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

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

interface UnifiedSettingsProps {
  embedded?: boolean;
  initialCategory?: SettingsCategory;
  onCategoryChange?: (category: SettingsCategory) => void;
}

// ============================================
// Category Configuration
// ============================================

const CATEGORIES: CategoryConfig[] = [
  {
    id: 'models',
    label: 'Models',
    description: 'Model defaults and availability',
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
  {
    id: 'appearance',
    label: 'Appearance',
    description: 'Card deck and display',
    icon: <Palette size={20} />,
  },
];

// ============================================
// Main Component
// ============================================

export function UnifiedSettings({ embedded = false, initialCategory, onCategoryChange }: UnifiedSettingsProps) {
  const { isDesktop, isMobile } = useViewport();
  const [activeCategory, setActiveCategory] = useState<SettingsCategory>(initialCategory || 'models');
  const [masterPanelOpen, setMasterPanelOpen] = useState(false);
  const [categorySheetOpen, setCategorySheetOpen] = useState(false);
  const [alert, setAlert] = useState<AlertState | null>(null);

  // Sync activeCategory when initialCategory prop changes (URL navigation)
  useEffect(() => {
    if (initialCategory) {
      setActiveCategory(initialCategory);
    }
  }, [initialCategory]);

  // Unified category change handler — updates local state + notifies parent for URL sync
  const handleCategoryChange = useCallback((category: SettingsCategory) => {
    setActiveCategory(category);
    onCategoryChange?.(category);
  }, [onCategoryChange]);

  // Models state - using hook for initial fetch, local state for optimistic updates
  const {
    data: fetchedModels,
    loading: modelsLoading,
  } = useAdminResource<Model[]>('/admin/api/models', {
    transform: (result) => (result as { models: Model[] }).models,
    onError: (err) => showAlert('error', err),
  });
  const [models, setModels] = useState<Model[] | null>(null);
  const [expandedProviders, setExpandedProviders] = useState<Set<string>>(new Set());

  // Sync local models with fetched data
  useEffect(() => {
    if (fetchedModels) {
      setModels(fetchedModels);
    }
  }, [fetchedModels]);

  // Capture state
  const [captureSettings, setCaptureSettings] = useState<CaptureSettingsData | null>(null);
  const [captureStats, setCaptureStats] = useState<CaptureStats | null>(null);
  const [captureLoading, setCaptureLoading] = useState(true);
  const [editedCapture, setEditedCapture] = useState<string>('');
  const [editedRetention, setEditedRetention] = useState<string>('');
  const [captureSaving, setCaptureSaving] = useState(false);

  // System settings state - each stores "provider:model" combined value
  const [systemSettings, setSystemSettings] = useState<SystemSettingsData | null>(null);
  const [systemLoading, setSystemLoading] = useState(true);
  const [editedGeneralModel, setEditedGeneralModel] = useState('');    // "openai:gpt-5-nano"
  const [editedFastModel, setEditedFastModel] = useState('');          // "openai:gpt-5-nano"
  const [editedImageModel, setEditedImageModel] = useState('');        // "runware:runware:101@1"
  const [editedAssistantModel, setEditedAssistantModel] = useState(''); // "deepseek:deepseek-reasoner"
  const [systemSaving, setSystemSaving] = useState(false);

  // Storage state
  const [storage, setStorage] = useState<StorageStats | null>(null);
  const [storageLoading, setStorageLoading] = useState(true);

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

  // Initialize expanded providers when models load
  useEffect(() => {
    if (models && models.length > 0 && expandedProviders.size === 0) {
      setExpandedProviders(new Set(models.map(m => m.provider)));
    }
  }, [models, expandedProviders.size]);

  // Model visibility states: 'off' | 'system' | 'users'
  type ModelVisibility = 'off' | 'system' | 'users';

  const getModelVisibility = (model: Model): ModelVisibility => {
    if (!model.enabled) return 'off';
    if (!model.user_enabled) return 'system';
    return 'users';
  };

  const setModelVisibility = async (modelId: number, visibility: ModelVisibility) => {
    const newEnabled = visibility !== 'off';
    const newUserEnabled = visibility === 'users';

    // Optimistic update
    setModels(prev => prev?.map(m =>
      m.id === modelId
        ? { ...m, enabled: newEnabled, user_enabled: newUserEnabled }
        : m
    ) ?? null);

    try {
      // Set both flags via two API calls (or we could add a new endpoint)
      const response = await adminFetch(`/admin/api/models/${modelId}/toggle`, {
        method: 'POST',
        body: JSON.stringify({ field: 'enabled', enabled: newEnabled }),
      });

      const data = await response.json();

      if (data.success && visibility === 'system') {
        // Need to explicitly turn off user_enabled
        await adminFetch(`/admin/api/models/${modelId}/toggle`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ field: 'user_enabled', enabled: false }),
        });
      } else if (data.success && visibility === 'users') {
        // Need to explicitly turn on user_enabled
        await adminFetch(`/admin/api/models/${modelId}/toggle`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ field: 'user_enabled', enabled: true }),
        });
      }

      if (!data.success) {
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
    if (!models) return {};
    return models.reduce((acc, model) => {
      if (!acc[model.provider]) {
        acc[model.provider] = [];
      }
      acc[model.provider].push(model);
      return acc;
    }, {} as Record<string, Model[]>);
  }, [models]);

  // Filtered models for system settings
  // General: all enabled models (excludes image-only models like DALL-E, Runware)
  const generalModels = useMemo(() =>
    models?.filter(m => m.enabled) || [], [models]);

  // Image: models that support image generation
  const imageModels = useMemo(() =>
    models?.filter(m => m.enabled && m.supports_image_gen) || [], [models]);

  // Fast: all enabled models (same pool as general)
  const fastModels = useMemo(() =>
    models?.filter(m => m.enabled) || [], [models]);

  // Reasoning: models that support reasoning
  const reasoningModels = useMemo(() =>
    models?.filter(m => m.enabled && m.supports_reasoning) || [], [models]);

  // ============================================
  // Capture Logic
  // ============================================

  const fetchCaptureData = useCallback(async () => {
    try {
      setCaptureLoading(true);
      const [settingsRes, statsRes] = await Promise.all([
        adminFetch(`/admin/api/settings`),
        adminFetch(`/admin/api/playground/stats`),
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
        updates.push(adminFetch(`/admin/api/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'LLM_PROMPT_CAPTURE', value: editedCapture }),
        }));
      }

      if (editedRetention !== captureSettings.LLM_PROMPT_RETENTION_DAYS.value) {
        updates.push(adminFetch(`/admin/api/settings`, {
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
      const response = await adminFetch(`/admin/api/settings/reset`, {
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
  // System Settings Logic
  // ============================================

  const fetchSystemData = useCallback(async () => {
    try {
      setSystemLoading(true);
      const response = await adminFetch(`/admin/api/settings`);
      const data = await response.json();

      if (data.success) {
        const settings = data.settings as SystemSettingsData;
        setSystemSettings(settings);
        // Initialize edited values as "provider:model" combined strings
        setEditedGeneralModel(`${settings.DEFAULT_PROVIDER.value}:${settings.DEFAULT_MODEL.value}`);
        setEditedFastModel(`${settings.FAST_PROVIDER.value}:${settings.FAST_MODEL.value}`);
        setEditedImageModel(`${settings.IMAGE_PROVIDER.value}:${settings.IMAGE_MODEL.value}`);
        setEditedAssistantModel(`${settings.ASSISTANT_PROVIDER.value}:${settings.ASSISTANT_MODEL.value}`);
      } else {
        showAlert('error', data.error || 'Failed to load system settings');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setSystemLoading(false);
    }
  }, []);

  const saveSystemSettings = async () => {
    if (!systemSettings) return;

    setSystemSaving(true);
    try {
      const updates: Promise<Response>[] = [];

      // Parse general model
      const [generalProvider, ...generalModelParts] = editedGeneralModel.split(':');
      const generalModel = generalModelParts.join(':');
      const originalGeneral = `${systemSettings.DEFAULT_PROVIDER.value}:${systemSettings.DEFAULT_MODEL.value}`;
      if (editedGeneralModel !== originalGeneral) {
        updates.push(adminFetch(`/admin/api/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'DEFAULT_PROVIDER', value: generalProvider }),
        }));
        updates.push(adminFetch(`/admin/api/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'DEFAULT_MODEL', value: generalModel }),
        }));
      }

      // Parse fast model
      const [fastProvider, ...fastModelParts] = editedFastModel.split(':');
      const fastModel = fastModelParts.join(':');
      const originalFast = `${systemSettings.FAST_PROVIDER.value}:${systemSettings.FAST_MODEL.value}`;
      if (editedFastModel !== originalFast) {
        updates.push(adminFetch(`/admin/api/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'FAST_PROVIDER', value: fastProvider }),
        }));
        updates.push(adminFetch(`/admin/api/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'FAST_MODEL', value: fastModel }),
        }));
      }

      // Parse image model
      const [imageProvider, ...imageModelParts] = editedImageModel.split(':');
      const imageModel = imageModelParts.join(':');
      const originalImage = `${systemSettings.IMAGE_PROVIDER.value}:${systemSettings.IMAGE_MODEL.value}`;
      if (editedImageModel !== originalImage) {
        updates.push(adminFetch(`/admin/api/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'IMAGE_PROVIDER', value: imageProvider }),
        }));
        updates.push(adminFetch(`/admin/api/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'IMAGE_MODEL', value: imageModel }),
        }));
      }

      // Parse assistant model
      const [assistantProvider, ...assistantModelParts] = editedAssistantModel.split(':');
      const assistantModel = assistantModelParts.join(':');
      const originalAssistant = `${systemSettings.ASSISTANT_PROVIDER.value}:${systemSettings.ASSISTANT_MODEL.value}`;
      if (editedAssistantModel !== originalAssistant) {
        updates.push(adminFetch(`/admin/api/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'ASSISTANT_PROVIDER', value: assistantProvider }),
        }));
        updates.push(adminFetch(`/admin/api/settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'ASSISTANT_MODEL', value: assistantModel }),
        }));
      }

      await Promise.all(updates);
      showAlert('success', 'System settings saved');
      await fetchSystemData();
    } catch {
      showAlert('error', 'Failed to save settings');
    } finally {
      setSystemSaving(false);
    }
  };

  const resetSystemSettings = async () => {
    setSystemSaving(true);
    try {
      // Reset all system settings to defaults
      const keys = [
        'DEFAULT_PROVIDER', 'DEFAULT_MODEL',
        'FAST_PROVIDER', 'FAST_MODEL',
        'IMAGE_PROVIDER', 'IMAGE_MODEL',
        'ASSISTANT_PROVIDER', 'ASSISTANT_MODEL',
      ];
      await Promise.all(keys.map(key =>
        adminFetch(`/admin/api/settings/reset`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key }),
        })
      ));
      showAlert('success', 'System settings reset to defaults');
      await fetchSystemData();
    } catch {
      showAlert('error', 'Failed to reset settings');
    } finally {
      setSystemSaving(false);
    }
  };

  const hasSystemChanges = systemSettings && (
    editedGeneralModel !== `${systemSettings.DEFAULT_PROVIDER.value}:${systemSettings.DEFAULT_MODEL.value}` ||
    editedFastModel !== `${systemSettings.FAST_PROVIDER.value}:${systemSettings.FAST_MODEL.value}` ||
    editedImageModel !== `${systemSettings.IMAGE_PROVIDER.value}:${systemSettings.IMAGE_MODEL.value}` ||
    editedAssistantModel !== `${systemSettings.ASSISTANT_PROVIDER.value}:${systemSettings.ASSISTANT_MODEL.value}`
  );

  // ============================================
  // Storage Logic
  // ============================================

  const fetchStorage = useCallback(async () => {
    try {
      setStorageLoading(true);
      const response = await adminFetch(`/admin/api/settings/storage`);
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

  // Load data when category changes (models handled by useAdminResource hook)
  useEffect(() => {
    if (activeCategory === 'models' && !systemSettings) {
      // Load system settings alongside models (they're now in the same tab)
      fetchSystemData();
    } else if (activeCategory === 'capture' && !captureSettings) {
      fetchCaptureData();
    } else if (activeCategory === 'storage' && !storage) {
      fetchStorage();
    }
    // Pricing is handled by the PricingManager component
  }, [activeCategory, systemSettings, captureSettings, storage, fetchSystemData, fetchCaptureData, fetchStorage]);

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

    // Helper to render a compact model selector card
    const renderModelSelectorCard = (
      label: string,
      description: string,
      value: string,
      onChange: (value: string) => void,
      availableModels: Model[],
      providerSetting: SettingConfig | undefined,
      modelSetting: SettingConfig | undefined
    ) => {
      const isOverridden = providerSetting?.is_db_override || modelSetting?.is_db_override;
      const defaultValue = providerSetting && modelSetting
        ? `${providerSetting.env_default}:${modelSetting.env_default}`
        : '';

      return (
        <div className="us-default-card">
          <div className="us-default-card__header">
            <h4 className="us-default-card__title">{label}</h4>
            {isOverridden && (
              <span className="admin-badge admin-badge--primary admin-badge--sm">Custom</span>
            )}
          </div>
          <p className="us-default-card__desc">{description}</p>
          <select
            className="admin-input admin-select us-default-card__select"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            disabled={systemSaving}
          >
            {availableModels.length === 0 ? (
              <option value="">No enabled models</option>
            ) : (
              availableModels.map((m) => (
                <option key={`${m.provider}:${m.model}`} value={`${m.provider}:${m.model}`}>
                  {m.provider} / {m.display_name || m.model}
                </option>
              ))
            )}
          </select>
          {defaultValue && defaultValue !== ':' && (
            <span className="us-default-card__default">
              Default: {defaultValue.replace(':', ' / ')}
            </span>
          )}
        </div>
      );
    };

    return (
      <div className="us-models">
        {/* Default Model Settings - 3 cards in a row */}
        {systemSettings && (
          <div className="us-defaults-section">
            <div className="us-defaults-section__header">
              <h3 className="us-defaults-section__title">System Models</h3>
              <div className="us-defaults-section__actions">
                <button
                  type="button"
                  className="admin-btn admin-btn--primary admin-btn--sm"
                  onClick={saveSystemSettings}
                  disabled={systemSaving || !hasSystemChanges}
                >
                  {systemSaving ? 'Saving...' : 'Save'}
                </button>
                <button
                  type="button"
                  className="admin-btn admin-btn--ghost admin-btn--sm"
                  onClick={resetSystemSettings}
                  disabled={systemSaving}
                >
                  Reset
                </button>
              </div>
            </div>
            <div className="us-defaults-grid">
              {renderModelSelectorCard(
                'General',
                'Personality generation, commentary, game support',
                editedGeneralModel,
                setEditedGeneralModel,
                generalModels,
                systemSettings.DEFAULT_PROVIDER,
                systemSettings.DEFAULT_MODEL
              )}
              {renderModelSelectorCard(
                'Fast',
                'Chat suggestions, categorization, quick tasks',
                editedFastModel,
                setEditedFastModel,
                fastModels,
                systemSettings.FAST_PROVIDER,
                systemSettings.FAST_MODEL
              )}
              {renderModelSelectorCard(
                'Image',
                'AI player avatar generation',
                editedImageModel,
                setEditedImageModel,
                imageModels,
                systemSettings.IMAGE_PROVIDER,
                systemSettings.IMAGE_MODEL
              )}
              {renderModelSelectorCard(
                'Assistant',
                'Experiment design, analysis, theme generation',
                editedAssistantModel,
                setEditedAssistantModel,
                reasoningModels,
                systemSettings.ASSISTANT_PROVIDER,
                systemSettings.ASSISTANT_MODEL
              )}
            </div>
          </div>
        )}

        {/* Loading state for system settings */}
        {!systemSettings && systemLoading && (
          <div className="us-defaults-section">
            <div className="us-defaults-section__header">
              <h3 className="us-defaults-section__title">System Models</h3>
            </div>
            <div className="us-defaults-grid us-defaults-grid--loading">
              <div className="us-default-card us-default-card--skeleton" />
              <div className="us-default-card us-default-card--skeleton" />
              <div className="us-default-card us-default-card--skeleton" />
              <div className="us-default-card us-default-card--skeleton" />
            </div>
          </div>
        )}

        {/* Available Models Section */}
        <div className="us-available-section">
          <h3 className="us-available-section__title">Available Models</h3>
        </div>

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
                    <div className="us-model__visibility">
                      {(['off', 'system', 'users'] as const).map((state) => {
                        const current = getModelVisibility(model);
                        // "System" appears enabled when current is 'system' OR 'users'
                        const isEnabled = state === current ||
                          (state === 'system' && current === 'users');
                        return (
                          <button
                            key={state}
                            type="button"
                            className={`us-visibility__btn us-visibility__btn--${state} ${isEnabled ? 'us-visibility__btn--active' : ''}`}
                            onClick={() => setModelVisibility(model.id, state)}
                          >
                            {state === 'off' ? 'Off' : state === 'system' ? 'System' : 'Users'}
                          </button>
                        );
                      })}
                    </div>
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
            className="admin-btn admin-btn--secondary"
            onClick={resetCaptureSettings}
            disabled={captureSaving}
          >
            Reset to Defaults
          </button>
          <button
            type="button"
            className="admin-btn admin-btn--primary"
            onClick={saveCaptureSettings}
            disabled={captureSaving || !hasCaptureChanges}
          >
            {captureSaving ? 'Saving...' : 'Save Changes'}
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

      </div>
    );
  };

  const renderPricingSection = () => {
    return <PricingManager embedded />;
  };

  const renderAppearanceSection = () => {
    return <DeckPackPicker />;
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
      case 'appearance':
        return renderAppearanceSection();
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

      {/* Master Panel - Category List (tablet/desktop only) */}
      <aside className={`admin-master ${masterPanelOpen || isDesktop ? 'admin-master--open' : ''}`}>
        <div className="admin-master__header">
          <h3 className="admin-master__title">Settings</h3>
        </div>
        <div className="admin-master__list">
          {CATEGORIES.map(category => (
            <button
              key={category.id}
              type="button"
              className={`admin-master__item ${activeCategory === category.id ? 'admin-master__item--selected' : ''}`}
              onClick={() => {
                handleCategoryChange(category.id);
                if (!isDesktop) setMasterPanelOpen(false);
              }}
            >
              <span className="admin-master__item-icon">{category.icon}</span>
              <span className="admin-master__item-name">{category.label}</span>
            </button>
          ))}
        </div>
      </aside>

      {/* Detail Panel */}
      <main className="admin-detail">
        {/* Mobile: category selector button + bottom sheet */}
        {isMobile && (
          <>
            <button
              type="button"
              className="us-category-trigger"
              onClick={() => setCategorySheetOpen(true)}
            >
              <span className="us-category-trigger__icon">{activeCategoryConfig?.icon}</span>
              <span className="us-category-trigger__label">{activeCategoryConfig?.label}</span>
              <Menu size={18} />
            </button>

            <MobileFilterSheet
              isOpen={categorySheetOpen}
              onClose={() => setCategorySheetOpen(false)}
              title="Settings"
            >
              <div className="us-category-sheet__list">
                {CATEGORIES.map(category => (
                  <button
                    key={category.id}
                    type="button"
                    className={`us-category-sheet__item ${activeCategory === category.id ? 'us-category-sheet__item--active' : ''}`}
                    onClick={() => {
                      handleCategoryChange(category.id);
                      setCategorySheetOpen(false);
                    }}
                  >
                    <span className="us-category-sheet__item-icon">{category.icon}</span>
                    <div className="us-category-sheet__item-text">
                      <span className="us-category-sheet__item-label">{category.label}</span>
                      <span className="us-category-sheet__item-desc">{category.description}</span>
                    </div>
                    {activeCategory === category.id && (
                      <Check size={18} className="us-category-sheet__item-check" />
                    )}
                  </button>
                ))}
              </div>
            </MobileFilterSheet>
          </>
        )}

        {/* Tablet: slide-out toggle */}
        {!isMobile && !isDesktop && (
          <button
            type="button"
            className="admin-master-toggle"
            onClick={() => setMasterPanelOpen(!masterPanelOpen)}
          >
            <Menu size={20} />
            <span>{activeCategoryConfig?.label || 'Settings'}</span>
          </button>
        )}

        {!isMobile && (
          <div className="admin-detail__header">
            <div>
              <h2 className="admin-detail__title">{activeCategoryConfig?.label}</h2>
              <p className="admin-detail__subtitle">{activeCategoryConfig?.description}</p>
            </div>
          </div>
        )}

        <div className="admin-detail__content">
          {renderContent()}
        </div>
      </main>

      {/* Backdrop for tablet sidebar */}
      {!isMobile && !isDesktop && masterPanelOpen && (
        <div
          className="us-backdrop"
          onClick={() => setMasterPanelOpen(false)}
        />
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

// ============================================
// Deck Pack Picker Component
// ============================================

const PREVIEW_ACES = ['spades', 'hearts', 'diamonds', 'clubs'] as const;
const PREVIEW_FACE_CARDS = [
  { rank: 'K', suit: 'spades' },
  { rank: 'Q', suit: 'spades' },
  { rank: 'J', suit: 'spades' },
  { rank: '10', suit: 'spades' },
];

function DeckPackPicker() {
  const { activePackId, setPackId } = useDeckPack();

  return (
    <div className="us-appearance">
      <h3 className="us-appearance__title">Card Deck</h3>
      <p className="us-appearance__subtitle">Choose the visual style for your playing cards</p>

      <div className="us-appearance__packs">
        {DECK_PACKS.map(pack => {
          const isActive = pack.id === activePackId;
          return (
            <button
              key={pack.id}
              type="button"
              className={`us-deck-pack ${isActive ? 'us-deck-pack--active' : ''}`}
              onClick={() => setPackId(pack.id)}
            >
              {/* Pack preview: stacked aces + face cards */}
              <div className="us-deck-pack__preview">
                {/* Stacked aces - spades on top */}
                <div className="us-deck-pack__aces">
                  {[...PREVIEW_ACES].reverse().map((suit, i) => {
                    const src = getCardImagePathForPack('A', suit, pack.id);
                    return (
                      <img
                        key={suit}
                        src={src}
                        alt={`Ace of ${suit}`}
                        className="us-deck-pack__ace-card"
                        style={{ zIndex: PREVIEW_ACES.length - i, marginLeft: i === 0 ? 0 : -28 }}
                      />
                    );
                  })}
                </div>
                {/* Face cards: K Q J 10 of spades */}
                {PREVIEW_FACE_CARDS.map(({ rank, suit }) => {
                  const src = getCardImagePathForPack(rank, suit, pack.id);
                  return (
                    <img
                      key={rank}
                      src={src}
                      alt={`${rank} of ${suit}`}
                      className="us-deck-pack__face-card"
                    />
                  );
                })}
              </div>

              {/* Pack info */}
              <div className="us-deck-pack__info">
                <span className="us-deck-pack__name">{pack.name}</span>
                <span className="us-deck-pack__desc">{pack.description}</span>
                {pack.attribution && (
                  <span className="us-deck-pack__license">{pack.license}</span>
                )}
              </div>

              {/* Selected indicator */}
              {isActive && (
                <div className="us-deck-pack__check">
                  <Check size={16} />
                </div>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default UnifiedSettings;
