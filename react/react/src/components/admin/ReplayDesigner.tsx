import { useState, useEffect, useMemo } from 'react';
import { adminAPI } from '../../utils/api';
import { Beaker, ChevronRight, ChevronLeft, Play, Plus, X, Check, AlertTriangle } from 'lucide-react';
import { CaptureSelector } from './CaptureSelector';
import { useLLMProviders } from '../../hooks/useLLMProviders';
import './AdminShared.css';
import './ReplayDesigner.css';

// ============================================
// Types
// ============================================

interface Variant {
  label: string;
  model?: string;
  provider?: string;
  personality?: string;
  guidance_injection?: string;
  reasoning_effort?: string;
}

interface CaptureSelection {
  mode: 'ids' | 'labels' | 'filters';
  ids?: number[];
  labels?: string[];
  match_all?: boolean;
  filters?: {
    phase?: string;
    action?: string;
    min_pot_odds?: number;
    max_pot_odds?: number;
  };
}

interface ReplayDesignerProps {
  onExperimentCreated?: (experimentId: number) => void;
}

type Step = 'captures' | 'variants' | 'review';

// ============================================
// Main Component
// ============================================

export function ReplayDesigner({ onExperimentCreated }: ReplayDesignerProps) {
  // Step state
  const [currentStep, setCurrentStep] = useState<Step>('captures');

  // Experiment config state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [hypothesis, setHypothesis] = useState('');

  // Capture selection state
  const [selectedCaptureIds, setSelectedCaptureIds] = useState<number[]>([]);
  const [captureSelection, setCaptureSelection] = useState<CaptureSelection>({
    mode: 'ids',
    ids: []
  });

  // Variants state
  const [variants, setVariants] = useState<Variant[]>([
    { label: 'Control' }
  ]);

  // UI state
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Fetch available models from system API
  const { providers, loading: providersLoading, formatModelLabel } = useLLMProviders({ scope: 'system' });

  // Build model options from providers
  const modelOptions = useMemo(() => {
    const options: { value: string; label: string; provider: string }[] = [];
    for (const provider of providers) {
      for (const model of provider.models) {
        options.push({
          value: model,
          label: formatModelLabel(provider.id, model),
          provider: provider.id
        });
      }
    }
    return options;
  }, [providers, formatModelLabel]);

  // Update capture selection when IDs change
  useEffect(() => {
    setCaptureSelection({
      ...captureSelection,
      mode: 'ids',
      ids: selectedCaptureIds
    });
  }, [selectedCaptureIds]);

  // Clear messages after timeout
  useEffect(() => {
    if (error || success) {
      const timer = setTimeout(() => {
        setError(null);
        setSuccess(null);
      }, 5000);
      return () => clearTimeout(timer);
    }
  }, [error, success]);

  // Add variant
  const addVariant = () => {
    setVariants([...variants, { label: `Variant ${variants.length + 1}` }]);
  };

  // Remove variant
  const removeVariant = (index: number) => {
    if (variants.length > 1) {
      setVariants(variants.filter((_, i) => i !== index));
    }
  };

  // Update variant
  const updateVariant = (index: number, updates: Partial<Variant>) => {
    setVariants(variants.map((v, i) =>
      i === index ? { ...v, ...updates } : v
    ));
  };

  // Apply model preset to variant
  const applyModelPreset = (index: number, modelValue: string) => {
    const preset = modelOptions.find(m => m.value === modelValue);
    if (preset) {
      updateVariant(index, {
        model: preset.value,
        provider: preset.provider
      });
    } else if (!modelValue) {
      // Clear model/provider when "Use original" is selected
      updateVariant(index, {
        model: undefined,
        provider: undefined
      });
    }
  };

  // Create experiment
  const createExperiment = async () => {
    if (!name.trim()) {
      setError('Please enter an experiment name');
      return;
    }

    if (selectedCaptureIds.length === 0) {
      setError('Please select at least one capture');
      return;
    }

    if (variants.length === 0) {
      setError('Please add at least one variant');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await adminAPI.fetch('/api/replay-experiments', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim() || undefined,
          hypothesis: hypothesis.trim() || undefined,
          capture_selection: {
            mode: 'ids',
            ids: selectedCaptureIds
          },
          variants: variants.filter(v => v.label.trim())
        })
      });

      const data = await response.json();

      if (data.success) {
        setSuccess(`Created experiment with ID ${data.experiment_id}`);
        if (onExperimentCreated) {
          onExperimentCreated(data.experiment_id);
        }
        // Reset form
        setName('');
        setDescription('');
        setHypothesis('');
        setSelectedCaptureIds([]);
        setVariants([{ label: 'Control' }]);
        setCurrentStep('captures');
      } else {
        setError(data.error || 'Failed to create experiment');
      }
    } catch (e) {
      setError('Failed to connect to server');
    } finally {
      setLoading(false);
    }
  };

  // Navigation
  const canProceed = () => {
    switch (currentStep) {
      case 'captures':
        return selectedCaptureIds.length > 0;
      case 'variants':
        return variants.length > 0 && variants.every(v => v.label.trim());
      case 'review':
        return name.trim() && selectedCaptureIds.length > 0 && variants.length > 0;
    }
  };

  const nextStep = () => {
    if (currentStep === 'captures' && canProceed()) setCurrentStep('variants');
    else if (currentStep === 'variants' && canProceed()) setCurrentStep('review');
  };

  const prevStep = () => {
    if (currentStep === 'variants') setCurrentStep('captures');
    else if (currentStep === 'review') setCurrentStep('variants');
  };

  return (
    <div className="rd-container">
      {/* Messages */}
      {error && (
        <div className="rd-alert rd-alert--error">
          <AlertTriangle size={18} />
          <span>{error}</span>
          <button onClick={() => setError(null)}><X size={16} /></button>
        </div>
      )}
      {success && (
        <div className="rd-alert rd-alert--success">
          <Check size={18} />
          <span>{success}</span>
          <button onClick={() => setSuccess(null)}><X size={16} /></button>
        </div>
      )}

      {/* Header */}
      <div className="rd-header">
        <div className="rd-header__icon">
          <Beaker size={24} />
        </div>
        <div className="rd-header__text">
          <h2>Create Replay Experiment</h2>
          <p>Re-run captured AI decisions with different variants</p>
        </div>
      </div>

      {/* Steps Indicator */}
      <div className="rd-steps">
        <button
          className={`rd-step ${currentStep === 'captures' ? 'rd-step--active' : ''} ${selectedCaptureIds.length > 0 ? 'rd-step--complete' : ''}`}
          onClick={() => setCurrentStep('captures')}
        >
          <span className="rd-step__number">1</span>
          <span className="rd-step__label">Select Captures</span>
          {selectedCaptureIds.length > 0 && (
            <span className="rd-step__badge">{selectedCaptureIds.length}</span>
          )}
        </button>
        <ChevronRight size={16} className="rd-steps__divider" />
        <button
          className={`rd-step ${currentStep === 'variants' ? 'rd-step--active' : ''} ${variants.length > 0 ? 'rd-step--complete' : ''}`}
          onClick={() => selectedCaptureIds.length > 0 && setCurrentStep('variants')}
          disabled={selectedCaptureIds.length === 0}
        >
          <span className="rd-step__number">2</span>
          <span className="rd-step__label">Configure Variants</span>
          {variants.length > 0 && (
            <span className="rd-step__badge">{variants.length}</span>
          )}
        </button>
        <ChevronRight size={16} className="rd-steps__divider" />
        <button
          className={`rd-step ${currentStep === 'review' ? 'rd-step--active' : ''}`}
          onClick={() => selectedCaptureIds.length > 0 && variants.length > 0 && setCurrentStep('review')}
          disabled={selectedCaptureIds.length === 0 || variants.length === 0}
        >
          <span className="rd-step__number">3</span>
          <span className="rd-step__label">Review & Launch</span>
        </button>
      </div>

      {/* Step Content */}
      <div className="rd-content">
        {/* Step 1: Select Captures */}
        {currentStep === 'captures' && (
          <div className="rd-step-content">
            <CaptureSelector
              embedded
              selectionMode
              selectedIds={selectedCaptureIds}
              onSelectionChange={setSelectedCaptureIds}
            />
          </div>
        )}

        {/* Step 2: Configure Variants */}
        {currentStep === 'variants' && (
          <div className="rd-step-content">
            <div className="rd-variants-header">
              <h3>Configure Variants</h3>
              <p>Each variant defines a different configuration to test against the captured decisions.</p>
            </div>

            <div className="rd-variants-list">
              {variants.map((variant, index) => (
                <div key={index} className="rd-variant-card">
                  <div className="rd-variant-card__header">
                    <input
                      type="text"
                      className="rd-variant-card__name"
                      value={variant.label}
                      onChange={(e) => updateVariant(index, { label: e.target.value })}
                      placeholder="Variant name..."
                    />
                    {variants.length > 1 && (
                      <button
                        className="rd-variant-card__remove"
                        onClick={() => removeVariant(index)}
                      >
                        <X size={16} />
                      </button>
                    )}
                  </div>

                  <div className="rd-variant-card__body">
                    {/* Model Selection */}
                    <div className="rd-field">
                      <label>Model</label>
                      <select
                        value={variant.model || ''}
                        onChange={(e) => applyModelPreset(index, e.target.value)}
                        disabled={providersLoading}
                      >
                        <option value="">Use original</option>
                        {modelOptions.map(m => (
                          <option key={`${m.provider}-${m.value}`} value={m.value}>
                            {m.label} ({m.provider})
                          </option>
                        ))}
                      </select>
                    </div>

                    {/* Guidance Injection */}
                    <div className="rd-field">
                      <label>Guidance Injection</label>
                      <textarea
                        value={variant.guidance_injection || ''}
                        onChange={(e) => updateVariant(index, { guidance_injection: e.target.value })}
                        placeholder="Extra instructions to inject into the prompt..."
                        rows={3}
                      />
                    </div>

                    {/* Reasoning Effort */}
                    <div className="rd-field">
                      <label>Reasoning Effort</label>
                      <select
                        value={variant.reasoning_effort || ''}
                        onChange={(e) => updateVariant(index, { reasoning_effort: e.target.value || undefined })}
                      >
                        <option value="">Default</option>
                        <option value="low">Low</option>
                        <option value="medium">Medium</option>
                        <option value="high">High</option>
                      </select>
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <button className="rd-add-variant" onClick={addVariant}>
              <Plus size={16} />
              Add Variant
            </button>
          </div>
        )}

        {/* Step 3: Review & Launch */}
        {currentStep === 'review' && (
          <div className="rd-step-content">
            <div className="rd-review">
              <div className="rd-review__section">
                <h3>Experiment Details</h3>
                <div className="rd-field">
                  <label>Name *</label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g., test_claude_on_mistakes"
                  />
                </div>
                <div className="rd-field">
                  <label>Description</label>
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="What are you testing?"
                    rows={2}
                  />
                </div>
                <div className="rd-field">
                  <label>Hypothesis</label>
                  <textarea
                    value={hypothesis}
                    onChange={(e) => setHypothesis(e.target.value)}
                    placeholder="What do you expect to find?"
                    rows={2}
                  />
                </div>
              </div>

              <div className="rd-review__section">
                <h3>Summary</h3>
                <div className="rd-review__summary">
                  <div className="rd-review__stat">
                    <span className="rd-review__stat-value">{selectedCaptureIds.length}</span>
                    <span className="rd-review__stat-label">Captures</span>
                  </div>
                  <span className="rd-review__times">x</span>
                  <div className="rd-review__stat">
                    <span className="rd-review__stat-value">{variants.length}</span>
                    <span className="rd-review__stat-label">Variants</span>
                  </div>
                  <span className="rd-review__equals">=</span>
                  <div className="rd-review__stat rd-review__stat--total">
                    <span className="rd-review__stat-value">{selectedCaptureIds.length * variants.length}</span>
                    <span className="rd-review__stat-label">Total Replays</span>
                  </div>
                </div>
              </div>

              <div className="rd-review__section">
                <h3>Variants</h3>
                <div className="rd-review__variants">
                  {variants.map((v, i) => (
                    <div key={i} className="rd-review__variant">
                      <span className="rd-review__variant-name">{v.label}</span>
                      <span className="rd-review__variant-config">
                        {v.model ? `${v.model}` : 'Original model'}
                        {v.guidance_injection ? ' + guidance' : ''}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Footer Navigation */}
      <div className="rd-footer">
        <div className="rd-footer__left">
          {currentStep !== 'captures' && (
            <button className="rd-btn rd-btn--secondary" onClick={prevStep}>
              <ChevronLeft size={16} />
              Back
            </button>
          )}
        </div>
        <div className="rd-footer__right">
          {currentStep !== 'review' ? (
            <button
              className="rd-btn rd-btn--primary"
              onClick={nextStep}
              disabled={!canProceed()}
            >
              Continue
              <ChevronRight size={16} />
            </button>
          ) : (
            <button
              className="rd-btn rd-btn--primary"
              onClick={createExperiment}
              disabled={loading || !canProceed()}
            >
              {loading ? (
                <>
                  <span className="rd-spinner" />
                  Creating...
                </>
              ) : (
                <>
                  <Play size={16} />
                  Create Experiment
                </>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default ReplayDesigner;
