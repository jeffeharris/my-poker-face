import { useState, useEffect, useCallback } from 'react';
import { adminAPI } from '../../utils/api';
import './ModelManager.css';

// ============================================
// Types
// ============================================

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

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

interface ModelManagerProps {
  embedded?: boolean;
}

// ============================================
// Main Component
// ============================================

export function ModelManager({ embedded = false }: ModelManagerProps) {
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [expandedProviders, setExpandedProviders] = useState<Set<string>>(new Set());

  // Fetch models
  const fetchModels = useCallback(async () => {
    try {
      setLoading(true);
      const response = await adminAPI.fetch('/admin/api/models');
      const data = await response.json();

      if (data.success) {
        setModels(data.models);
        // Expand all providers by default
        const providers = new Set<string>(data.models.map((m: Model) => m.provider));
        setExpandedProviders(providers);
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to load models' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  // Toggle model enabled status
  const toggleModel = async (modelId: number, enabled: boolean) => {
    try {
      const response = await adminAPI.fetch(`/admin/api/models/${modelId}/toggle`, {
        method: 'POST',
        body: JSON.stringify({ enabled }),
      });

      const data = await response.json();

      if (data.success) {
        setModels(prev => prev.map(m =>
          m.id === modelId ? { ...m, enabled } : m
        ));
        setAlert({ type: 'success', message: `Model ${enabled ? 'enabled' : 'disabled'}` });
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to update model' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  // Toggle provider expansion
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

  // Group models by provider
  const modelsByProvider = models.reduce((acc, model) => {
    if (!acc[model.provider]) {
      acc[model.provider] = [];
    }
    acc[model.provider].push(model);
    return acc;
  }, {} as Record<string, Model[]>);

  // Clear alert after timeout
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  if (loading) {
    return (
      <div className="mm-loading">
        <div className="mm-loading__spinner" />
        <span>Loading models...</span>
      </div>
    );
  }

  return (
    <div className={`mm-container ${embedded ? 'mm-container--embedded' : ''}`}>
      {/* Alert Toast */}
      {alert && (
        <div className={`mm-alert mm-alert--${alert.type}`}>
          <span className="mm-alert__icon">
            {alert.type === 'success' ? '✓' : alert.type === 'error' ? '✕' : 'i'}
          </span>
          <span className="mm-alert__message">{alert.message}</span>
          <button className="mm-alert__close" onClick={() => setAlert(null)}>×</button>
        </div>
      )}

      {/* Header */}
      <div className="mm-header">
        <h2 className="mm-header__title">Model Management</h2>
        <p className="mm-header__subtitle">Enable or disable LLM models available in the game</p>
      </div>

      {/* Provider Groups */}
      <div className="mm-providers">
        {Object.entries(modelsByProvider).map(([provider, providerModels]) => (
          <div key={provider} className="mm-provider">
            <button
              className="mm-provider__header"
              onClick={() => toggleProvider(provider)}
              type="button"
            >
              <span className={`mm-provider__chevron ${expandedProviders.has(provider) ? 'mm-provider__chevron--open' : ''}`}>
                <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                  <path d="M7.5 5L12.5 10L7.5 15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </span>
              <span className="mm-provider__name">{provider}</span>
              <span className="mm-provider__count">
                {providerModels.filter(m => m.enabled).length}/{providerModels.length} enabled
              </span>
            </button>

            {expandedProviders.has(provider) && (
              <div className="mm-provider__models">
                {providerModels.map(model => (
                  <div key={model.id} className={`mm-model ${!model.enabled ? 'mm-model--disabled' : ''}`}>
                    <div className="mm-model__info">
                      <span className="mm-model__name">
                        {model.display_name || model.model}
                      </span>
                      <div className="mm-model__capabilities">
                        {model.supports_reasoning && (
                          <span className="mm-cap mm-cap--reasoning" title="Supports reasoning">R</span>
                        )}
                        {model.supports_json_mode && (
                          <span className="mm-cap mm-cap--json" title="Supports JSON mode">J</span>
                        )}
                        {model.supports_image_gen && (
                          <span className="mm-cap mm-cap--image" title="Supports image generation">I</span>
                        )}
                      </div>
                    </div>
                    <label className="mm-toggle">
                      <input
                        type="checkbox"
                        checked={model.enabled}
                        onChange={(e) => toggleModel(model.id, e.target.checked)}
                      />
                      <span className="mm-toggle__slider" />
                    </label>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Legend */}
      <div className="mm-legend">
        <span className="mm-legend__title">Capabilities:</span>
        <span className="mm-cap mm-cap--reasoning">R</span> Reasoning
        <span className="mm-cap mm-cap--json">J</span> JSON Mode
        <span className="mm-cap mm-cap--image">I</span> Image Gen
      </div>
    </div>
  );
}

export default ModelManager;
