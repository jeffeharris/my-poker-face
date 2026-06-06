import { useState, useEffect, useMemo, useCallback } from 'react';
import { adminFetch } from '../../../../utils/api';
import { useAdminResource } from '../../../../hooks/useAdminResource';
import type {
  Model,
  ModelVisibility,
  SettingConfig,
  SystemSettingsData,
  ShowAlert,
} from '../types';
import './ModelsSection.css';

interface ModelsSectionProps {
  showAlert: ShowAlert;
}

export function ModelsSection({ showAlert }: ModelsSectionProps) {
  // Models state - using hook for initial fetch, local state for optimistic updates
  const { data: fetchedModels, loading: modelsLoading } = useAdminResource<Model[]>(
    '/admin/api/models',
    {
      transform: (result) => (result as { models: Model[] }).models,
      onError: (err) => showAlert('error', err),
    }
  );
  const [models, setModels] = useState<Model[] | null>(null);
  const [expandedProviders, setExpandedProviders] = useState<Set<string>>(new Set());

  // System settings state - each stores "provider:model" combined value
  const [systemSettings, setSystemSettings] = useState<SystemSettingsData | null>(null);
  const [systemLoading, setSystemLoading] = useState(true);
  const [editedGeneralModel, setEditedGeneralModel] = useState('');
  const [editedFastModel, setEditedFastModel] = useState('');
  const [editedNanoModel, setEditedNanoModel] = useState(''); // "groq:llama-3.1-8b-instant"
  const [editedImageModel, setEditedImageModel] = useState('');
  const [editedAssistantModel, setEditedAssistantModel] = useState('');
  const [systemSaving, setSystemSaving] = useState(false);

  // Sync local models with fetched data
  useEffect(() => {
    if (fetchedModels) {
      setModels(fetchedModels);
    }
  }, [fetchedModels]);

  // Initialize expanded providers when models load
  useEffect(() => {
    if (models && models.length > 0 && expandedProviders.size === 0) {
      setExpandedProviders(new Set(models.map((m) => m.provider)));
    }
  }, [models, expandedProviders.size]);

  const getModelVisibility = (model: Model): ModelVisibility => {
    if (!model.enabled) return 'off';
    if (!model.user_enabled) return 'system';
    return 'users';
  };

  const setModelVisibility = async (modelId: number, visibility: ModelVisibility) => {
    const newEnabled = visibility !== 'off';
    const newUserEnabled = visibility === 'users';

    // Optimistic update
    setModels(
      (prev) =>
        prev?.map((m) =>
          m.id === modelId ? { ...m, enabled: newEnabled, user_enabled: newUserEnabled } : m
        ) ?? null
    );

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
    setExpandedProviders((prev) => {
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
    return models.reduce(
      (acc, model) => {
        if (!acc[model.provider]) {
          acc[model.provider] = [];
        }
        acc[model.provider].push(model);
        return acc;
      },
      {} as Record<string, Model[]>
    );
  }, [models]);

  // Filtered models for system settings
  // General: all enabled models (excludes image-only models like DALL-E, Runware)
  const generalModels = useMemo(() => models?.filter((m) => m.enabled) || [], [models]);

  // Image: models that support image generation
  const imageModels = useMemo(
    () => models?.filter((m) => m.enabled && m.supports_image_gen) || [],
    [models]
  );

  // Fast: all enabled models (same pool as general)
  const fastModels = useMemo(() => models?.filter((m) => m.enabled) || [], [models]);

  // Reasoning: models that support reasoning
  const reasoningModels = useMemo(
    () => models?.filter((m) => m.enabled && m.supports_reasoning) || [],
    [models]
  );

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
        setEditedNanoModel(`${settings.NANO_PROVIDER.value}:${settings.NANO_MODEL.value}`);
        setEditedImageModel(`${settings.IMAGE_PROVIDER.value}:${settings.IMAGE_MODEL.value}`);
        setEditedAssistantModel(
          `${settings.ASSISTANT_PROVIDER.value}:${settings.ASSISTANT_MODEL.value}`
        );
      } else {
        showAlert('error', data.error || 'Failed to load system settings');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setSystemLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load system settings on mount (this section only mounts when models tab is active)
  useEffect(() => {
    fetchSystemData();
  }, [fetchSystemData]);

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
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'DEFAULT_PROVIDER', value: generalProvider }),
          })
        );
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'DEFAULT_MODEL', value: generalModel }),
          })
        );
      }

      // Parse fast model
      const [fastProvider, ...fastModelParts] = editedFastModel.split(':');
      const fastModel = fastModelParts.join(':');
      const originalFast = `${systemSettings.FAST_PROVIDER.value}:${systemSettings.FAST_MODEL.value}`;
      if (editedFastModel !== originalFast) {
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'FAST_PROVIDER', value: fastProvider }),
          })
        );
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'FAST_MODEL', value: fastModel }),
          })
        );
      }

      // Parse nano model
      const [nanoProvider, ...nanoModelParts] = editedNanoModel.split(':');
      const nanoModel = nanoModelParts.join(':');
      const originalNano = `${systemSettings.NANO_PROVIDER.value}:${systemSettings.NANO_MODEL.value}`;
      if (editedNanoModel !== originalNano) {
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'NANO_PROVIDER', value: nanoProvider }),
          })
        );
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'NANO_MODEL', value: nanoModel }),
          })
        );
      }

      // Parse image model
      const [imageProvider, ...imageModelParts] = editedImageModel.split(':');
      const imageModel = imageModelParts.join(':');
      const originalImage = `${systemSettings.IMAGE_PROVIDER.value}:${systemSettings.IMAGE_MODEL.value}`;
      if (editedImageModel !== originalImage) {
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'IMAGE_PROVIDER', value: imageProvider }),
          })
        );
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'IMAGE_MODEL', value: imageModel }),
          })
        );
      }

      // Parse assistant model
      const [assistantProvider, ...assistantModelParts] = editedAssistantModel.split(':');
      const assistantModel = assistantModelParts.join(':');
      const originalAssistant = `${systemSettings.ASSISTANT_PROVIDER.value}:${systemSettings.ASSISTANT_MODEL.value}`;
      if (editedAssistantModel !== originalAssistant) {
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'ASSISTANT_PROVIDER', value: assistantProvider }),
          })
        );
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'ASSISTANT_MODEL', value: assistantModel }),
          })
        );
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
        'DEFAULT_PROVIDER',
        'DEFAULT_MODEL',
        'FAST_PROVIDER',
        'FAST_MODEL',
        'NANO_PROVIDER',
        'NANO_MODEL',
        'IMAGE_PROVIDER',
        'IMAGE_MODEL',
        'ASSISTANT_PROVIDER',
        'ASSISTANT_MODEL',
      ];
      await Promise.all(
        keys.map((key) =>
          adminFetch(`/admin/api/settings/reset`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key }),
          })
        )
      );
      showAlert('success', 'System settings reset to defaults');
      await fetchSystemData();
    } catch {
      showAlert('error', 'Failed to reset settings');
    } finally {
      setSystemSaving(false);
    }
  };

  const hasSystemChanges =
    systemSettings &&
    (editedGeneralModel !==
      `${systemSettings.DEFAULT_PROVIDER.value}:${systemSettings.DEFAULT_MODEL.value}` ||
      editedFastModel !==
        `${systemSettings.FAST_PROVIDER.value}:${systemSettings.FAST_MODEL.value}` ||
      editedNanoModel !==
        `${systemSettings.NANO_PROVIDER.value}:${systemSettings.NANO_MODEL.value}` ||
      editedImageModel !==
        `${systemSettings.IMAGE_PROVIDER.value}:${systemSettings.IMAGE_MODEL.value}` ||
      editedAssistantModel !==
        `${systemSettings.ASSISTANT_PROVIDER.value}:${systemSettings.ASSISTANT_MODEL.value}`);

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
    const defaultValue =
      providerSetting && modelSetting
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
              'Player-read flavor: chat suggestions, lobby narration',
              editedFastModel,
              setEditedFastModel,
              fastModels,
              systemSettings.FAST_PROVIDER,
              systemSettings.FAST_MODEL
            )}
            {renderModelSelectorCard(
              'Nano',
              'Mechanical, never-read tasks: beat cleanup, categorization',
              editedNanoModel,
              setEditedNanoModel,
              fastModels,
              systemSettings.NANO_PROVIDER,
              systemSettings.NANO_MODEL
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
            <span
              className={`us-provider__chevron ${expandedProviders.has(provider) ? 'us-provider__chevron--open' : ''}`}
            >
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <path
                  d="M7.5 5L12.5 10L7.5 15"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            <span className="us-provider__name">{provider}</span>
            <span className="us-provider__count">
              {providerModels.filter((m) => m.enabled).length}/{providerModels.length} enabled
            </span>
          </button>

          {expandedProviders.has(provider) && (
            <div className="us-provider__models">
              {providerModels.map((model) => (
                <div
                  key={model.id}
                  className={`us-model ${!model.enabled ? 'us-model--disabled' : ''}`}
                >
                  <div className="us-model__info">
                    <span className="us-model__name">{model.display_name || model.model}</span>
                    <div className="us-model__capabilities">
                      {model.supports_reasoning && (
                        <span className="us-cap us-cap--reasoning" title="Supports reasoning">
                          R
                        </span>
                      )}
                      {model.supports_json_mode && (
                        <span className="us-cap us-cap--json" title="Supports JSON mode">
                          J
                        </span>
                      )}
                      {model.supports_image_gen && (
                        <span className="us-cap us-cap--image" title="Supports image generation">
                          I
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="us-model__visibility">
                    {(['off', 'system', 'users'] as const).map((state) => {
                      const current = getModelVisibility(model);
                      // "System" appears enabled when current is 'system' OR 'users'
                      const isEnabled =
                        state === current || (state === 'system' && current === 'users');
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
}
