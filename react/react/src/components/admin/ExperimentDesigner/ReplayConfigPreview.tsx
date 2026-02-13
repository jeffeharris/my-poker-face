import { useState, useEffect } from 'react';
import { Play, Code, Settings, Plus, Trash2, ChevronDown, ChevronRight, Loader2, AlertCircle, AlertTriangle, Database } from 'lucide-react';
import type { ReplayExperimentConfig, ReplayVariantConfig } from './types';
import { adminFetch } from '../../../utils/api';
import { useLLMProviders } from '../../../hooks/useLLMProviders';
import { CaptureSelector } from '../CaptureSelector';

interface ReplayConfigPreviewProps {
  config: ReplayExperimentConfig;
  onConfigUpdate: (updates: Partial<ReplayExperimentConfig>) => void;
  onLaunch: () => void;
  sessionId?: string | null;
}

type ViewMode = 'form' | 'json';

interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export function ReplayConfigPreview({ config, onConfigUpdate, onLaunch, sessionId: _sessionId }: ReplayConfigPreviewProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('form');
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [variantsExpanded, setVariantsExpanded] = useState(true);
  const [capturesExpanded, setCapturesExpanded] = useState(true);

  const {
    providers,
    loading: providersLoading,
    getModelsForProvider,
  } = useLLMProviders({ scope: 'system' });

  // Sync JSON text when config changes
  useEffect(() => {
    if (viewMode === 'json') {
      setJsonText(JSON.stringify(config, null, 2));
    }
  }, [config, viewMode]);

  const handleJsonChange = (text: string) => {
    setJsonText(text);
    try {
      const parsed = JSON.parse(text);
      setJsonError(null);
      onConfigUpdate(parsed);
    } catch {
      setJsonError('Invalid JSON');
    }
  };

  // Validation
  const validation: ValidationResult = {
    valid: true,
    errors: [],
    warnings: [],
  };

  if (!config.name.trim()) {
    validation.valid = false;
    validation.errors.push('Name is required');
  }

  if (config.variants.length === 0) {
    validation.valid = false;
    validation.errors.push('At least one variant is required');
  }

  const selectedCaptureIds = config.capture_selection.ids || [];
  if (selectedCaptureIds.length === 0) {
    validation.valid = false;
    validation.errors.push('Select at least one capture');
  }

  if (config.variants.length === 1) {
    validation.warnings.push('Consider adding a second variant for comparison');
  }

  const handleLaunch = async () => {
    if (!validation.valid || launching) return;

    setLaunching(true);
    setLaunchError(null);

    try {
      const response = await adminFetch('/api/replay-experiments', {
        method: 'POST',
        body: JSON.stringify(config),
      });

      const data = await response.json();

      if (data.success) {
        onLaunch();
      } else {
        setLaunchError(data.error || 'Failed to create experiment');
      }
    } catch {
      setLaunchError('Failed to connect to server');
    } finally {
      setLaunching(false);
    }
  };

  const handleCaptureSelectionChange = (ids: number[]) => {
    onConfigUpdate({
      capture_selection: {
        ...config.capture_selection,
        mode: 'ids',
        ids,
      },
    });
  };

  const addVariant = () => {
    const newVariant: ReplayVariantConfig = {
      label: `Variant ${config.variants.length + 1}`,
    };
    onConfigUpdate({ variants: [...config.variants, newVariant] });
  };

  const removeVariant = (index: number) => {
    if (config.variants.length <= 1) return;
    onConfigUpdate({
      variants: config.variants.filter((_, i) => i !== index),
    });
  };

  const updateVariant = (index: number, updates: Partial<ReplayVariantConfig>) => {
    const newVariants = [...config.variants];
    newVariants[index] = { ...newVariants[index], ...updates };
    onConfigUpdate({ variants: newVariants });
  };

  return (
    <div className="config-preview">
      {/* Header */}
      <div className="config-preview__header">
        <div className="config-preview__header-left">
          <h3 className="config-preview__title">Replay Experiment</h3>
        </div>
        <div className="config-preview__view-toggle">
          <button
            className={`config-preview__view-btn ${viewMode === 'form' ? 'config-preview__view-btn--active' : ''}`}
            onClick={() => setViewMode('form')}
            type="button"
          >
            <Settings size={14} />
            Form
          </button>
          <button
            className={`config-preview__view-btn ${viewMode === 'json' ? 'config-preview__view-btn--active' : ''}`}
            onClick={() => setViewMode('json')}
            type="button"
          >
            <Code size={14} />
            JSON
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="config-preview__content">
        {viewMode === 'form' ? (
          <div className="config-preview__form">
            {/* Basic Info */}
            <div className="config-preview__section">
              <label className="config-preview__label">
                Name *
                <input
                  type="text"
                  className={`config-preview__input ${!config.name ? 'config-preview__input--error' : ''}`}
                  value={config.name}
                  onChange={(e) => onConfigUpdate({ name: e.target.value })}
                  placeholder="e.g., claude_vs_gpt_preflop"
                />
              </label>

              <label className="config-preview__label">
                Description
                <textarea
                  className="config-preview__input config-preview__textarea"
                  rows={2}
                  value={config.description}
                  onChange={(e) => onConfigUpdate({ description: e.target.value })}
                  placeholder="What this experiment tests"
                />
              </label>

              <label className="config-preview__label">
                Hypothesis
                <textarea
                  className="config-preview__input config-preview__textarea"
                  rows={2}
                  value={config.hypothesis}
                  onChange={(e) => onConfigUpdate({ hypothesis: e.target.value })}
                  placeholder="Expected outcome"
                />
              </label>
            </div>

            {/* Capture Selection */}
            <div className="config-preview__section">
              <button
                className="config-preview__section-toggle"
                onClick={() => setCapturesExpanded(!capturesExpanded)}
                type="button"
              >
                {capturesExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                <Database size={16} />
                <span>Captures</span>
                <span className="config-preview__section-badge">
                  {selectedCaptureIds.length} selected
                </span>
              </button>

              {capturesExpanded && (
                <div className="config-preview__section-content">
                  <div className="config-preview__capture-selector">
                    <CaptureSelector
                      embedded
                      selectionMode
                      selectedIds={selectedCaptureIds}
                      onSelectionChange={handleCaptureSelectionChange}
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Variants */}
            <div className="config-preview__section">
              <button
                className="config-preview__section-toggle"
                onClick={() => setVariantsExpanded(!variantsExpanded)}
                type="button"
              >
                {variantsExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                <span>Variants</span>
                <span className="config-preview__section-badge">
                  {config.variants.length}
                </span>
              </button>

              {variantsExpanded && (
                <div className="config-preview__section-content">
                  <p className="config-preview__help-text">
                    Each variant will replay the selected captures with different model/prompt settings.
                    The first variant typically uses original settings as a control.
                  </p>

                  {config.variants.map((variant, index) => (
                    <div key={index} className="config-preview__variant">
                      <div className="config-preview__variant-header">
                        <input
                          type="text"
                          className="config-preview__input config-preview__input--inline"
                          value={variant.label}
                          onChange={(e) => updateVariant(index, { label: e.target.value })}
                          placeholder="Variant name"
                        />
                        {config.variants.length > 1 && (
                          <button
                            className="config-preview__remove-btn"
                            onClick={() => removeVariant(index)}
                            type="button"
                            title="Remove variant"
                          >
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>

                      <div className="config-preview__variant-fields">
                        <label className="config-preview__label config-preview__label--inline">
                          Provider
                          <select
                            className="config-preview__select"
                            value={variant.provider || ''}
                            onChange={(e) => updateVariant(index, {
                              provider: e.target.value || undefined,
                              model: undefined,
                            })}
                            disabled={providersLoading}
                          >
                            <option value="">Original</option>
                            {providers.map((p) => (
                              <option key={p.id} value={p.id}>{p.name}</option>
                            ))}
                          </select>
                        </label>

                        {variant.provider && (
                          <label className="config-preview__label config-preview__label--inline">
                            Model
                            <select
                              className="config-preview__select"
                              value={variant.model || ''}
                              onChange={(e) => updateVariant(index, { model: e.target.value || undefined })}
                            >
                              <option value="">Default</option>
                              {getModelsForProvider(variant.provider).map((model) => (
                                <option key={model} value={model}>{model}</option>
                              ))}
                            </select>
                          </label>
                        )}
                      </div>

                      <label className="config-preview__label">
                        Guidance Injection
                        <textarea
                          className="config-preview__input config-preview__textarea"
                          rows={2}
                          value={variant.guidance_injection || ''}
                          onChange={(e) => updateVariant(index, { guidance_injection: e.target.value || undefined })}
                          placeholder="Extra instructions appended to the prompt (optional)"
                        />
                      </label>
                    </div>
                  ))}

                  <button
                    className="config-preview__add-variant-btn"
                    onClick={addVariant}
                    type="button"
                  >
                    <Plus size={14} />
                    Add Variant
                  </button>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="config-preview__json-container">
            <textarea
              className="config-preview__json-editor"
              value={jsonText}
              onChange={(e) => handleJsonChange(e.target.value)}
              spellCheck={false}
            />
            {jsonError && (
              <div className="config-preview__json-error">{jsonError}</div>
            )}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="config-preview__footer">
        {/* Validation Messages */}
        {validation.errors.length > 0 && (
          <div className="config-preview__validation config-preview__validation--error">
            <AlertCircle size={14} />
            <span>{validation.errors[0]}</span>
          </div>
        )}
        {validation.errors.length === 0 && validation.warnings.length > 0 && (
          <div className="config-preview__validation config-preview__validation--warning">
            <AlertTriangle size={14} />
            <span>{validation.warnings[0]}</span>
          </div>
        )}

        {launchError && (
          <div className="config-preview__validation config-preview__validation--error">
            <AlertCircle size={14} />
            <span>{launchError}</span>
          </div>
        )}

        {/* Summary */}
        <div className="config-preview__summary">
          <span>{selectedCaptureIds.length} captures</span>
          <span>×</span>
          <span>{config.variants.length} variants</span>
          <span>=</span>
          <span>{selectedCaptureIds.length * config.variants.length} total runs</span>
        </div>

        <button
          className="config-preview__launch-btn"
          onClick={handleLaunch}
          disabled={!validation.valid || launching}
          type="button"
        >
          {launching ? (
            <>
              <Loader2 size={18} className="animate-spin" />
              Creating...
            </>
          ) : (
            <>
              <Play size={18} />
              Create Experiment
            </>
          )}
        </button>
      </div>
    </div>
  );
}

export default ReplayConfigPreview;
