import { useState, useEffect, useCallback } from 'react';
import { Play, Code, Settings, ChevronDown, ChevronRight, ChevronLeft, AlertCircle, AlertTriangle, Loader2, Plus, Trash2, FlaskConical, Zap, Users, Tag, X, Shuffle } from 'lucide-react';
import type { ExperimentConfig, PromptConfig, ControlConfig, VariantConfig, ConfigVersion } from './types';
import { DEFAULT_PROMPT_CONFIG } from './types';
import { config as appConfig } from '../../../config';
import { useLLMProviders } from '../../../hooks/useLLMProviders';
import { seedToWords, wordsToSeed, generateSeed, isWordSeed } from './seedWords';

// Number input field defaults for when blur occurs with empty value
const NUMBER_FIELD_DEFAULTS: Record<string, number> = {
  num_tournaments: 1,
  hands_per_tournament: 10,
  num_players: 4,
  starting_stack: 2000,
  big_blind: 100,
  parallel_tournaments: 1,
  stagger_start_delay: 2,
};

interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

interface ConfigPreviewProps {
  config: ExperimentConfig;
  onConfigUpdate: (updates: Partial<ExperimentConfig>) => void;
  onLaunch: () => void;
  /** Session ID for the design chat, passed to backend to save design history */
  sessionId?: string | null;
  configVersions?: ConfigVersion[];
  currentVersionIndex?: number;
  onVersionChange?: (index: number) => void;
}

type ViewMode = 'form' | 'json';

const PROMPT_CONFIG_LABELS: Record<keyof PromptConfig, string> = {
  pot_odds: 'Pot Odds Guidance',
  hand_strength: 'Hand Strength Evaluation',
  session_memory: 'Session Memory',
  opponent_intel: 'Opponent Intelligence',
  strategic_reflection: 'Strategic Reflection',
  chattiness: 'Chattiness Guidance',
  emotional_state: 'Emotional State',
  tilt_effects: 'Tilt Effects',
  mind_games: 'Mind Games Instruction',
  persona_response: 'Persona Response',
  memory_keep_exchanges: 'Memory Exchanges',
};

export function ConfigPreview({ config, onConfigUpdate, onLaunch, sessionId, configVersions, currentVersionIndex, onVersionChange }: ConfigPreviewProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('form');
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [promptConfigExpanded, setPromptConfigExpanded] = useState(false);
  const [abTestingExpanded, setAbTestingExpanded] = useState(false);
  const [advancedExpanded, setAdvancedExpanded] = useState(false);
  const [availablePersonalities, setAvailablePersonalities] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');
  // Store seed when disabled so we can restore it when re-enabled
  const [savedSeed, setSavedSeed] = useState<number>(() => config.random_seed ?? generateSeed());

  // Use system scope for admin experiment designer
  const {
    providers,
    loading: providersLoading,
    getModelsForProvider,
    getDefaultModel,
  } = useLLMProviders({ scope: 'system' });

  // Fetch available personalities on mount
  useEffect(() => {
    const fetchPersonalities = async () => {
      try {
        const response = await fetch(`${appConfig.API_URL}/api/experiments/personalities`, {
          credentials: 'include',
        });
        const data = await response.json();
        if (data.success && data.personalities) {
          setAvailablePersonalities(data.personalities);
        }
      } catch (err) {
        console.warn('Failed to fetch personalities:', err);
      }
    };
    fetchPersonalities();
  }, []);


  // Sync JSON text when config changes (if in form mode)
  useEffect(() => {
    if (viewMode === 'form') {
      setJsonText(JSON.stringify(config, null, 2));
      setJsonError(null);
    }
  }, [config, viewMode]);

  // Validate when config changes
  useEffect(() => {
    const validateConfig = async () => {
      if (!config.name) {
        setValidation(null);
        return;
      }

      setValidating(true);
      try {
        const response = await fetch(`${appConfig.API_URL}/api/experiments/validate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ config }),
        });
        const data = await response.json();
        setValidation(data);
      } catch (err) {
        console.error('Validation request failed:', err);
        setValidation({ valid: false, errors: ['Failed to validate configuration'], warnings: [] });
      } finally {
        setValidating(false);
      }
    };

    const debounce = setTimeout(validateConfig, 500);
    return () => clearTimeout(debounce);
  }, [config]);

  const handleJsonChange = (text: string) => {
    setJsonText(text);
    try {
      const parsed = JSON.parse(text);
      setJsonError(null);
      onConfigUpdate(parsed);
    } catch (e) {
      const errorMessage = e instanceof SyntaxError ? e.message : 'Invalid JSON';
      setJsonError(errorMessage);
    }
  };

  const handleFieldChange = (field: keyof ExperimentConfig, value: unknown) => {
    onConfigUpdate({ [field]: value });
  };

  // Handle number input change - allows empty string while typing
  const handleNumberChange = (field: keyof ExperimentConfig, value: string) => {
    if (value === '') {
      // Allow empty temporarily - will be fixed on blur
      onConfigUpdate({ [field]: '' as unknown as number });
    } else {
      const parsed = parseInt(value);
      if (!isNaN(parsed)) {
        onConfigUpdate({ [field]: parsed });
      }
    }
  };

  // Handle number input blur - apply default if empty
  const handleNumberBlur = (field: keyof ExperimentConfig, value: string) => {
    if (value === '' || isNaN(parseInt(value))) {
      const defaultVal = NUMBER_FIELD_DEFAULTS[field] ?? 1;
      onConfigUpdate({ [field]: defaultVal });
    }
  };

  // Handle float input change (for stagger_start_delay)
  const handleFloatChange = (field: keyof ExperimentConfig, value: string) => {
    if (value === '') {
      onConfigUpdate({ [field]: '' as unknown as number });
    } else {
      const parsed = parseFloat(value);
      if (!isNaN(parsed)) {
        onConfigUpdate({ [field]: parsed });
      }
    }
  };

  const handleFloatBlur = (field: keyof ExperimentConfig, value: string) => {
    if (value === '' || isNaN(parseFloat(value))) {
      const defaultVal = NUMBER_FIELD_DEFAULTS[field] ?? 0;
      onConfigUpdate({ [field]: defaultVal });
    }
  };

  // Tag management
  const handleAddTag = () => {
    const tag = tagInput.trim().toLowerCase().replace(/\s+/g, '_');
    if (tag && !config.tags.includes(tag)) {
      onConfigUpdate({ tags: [...config.tags, tag] });
    }
    setTagInput('');
  };

  const handleRemoveTag = (tagToRemove: string) => {
    onConfigUpdate({ tags: config.tags.filter(t => t !== tagToRemove) });
  };

  const handleTagKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleAddTag();
    }
  };

  // Personality management
  const handlePersonalityToggle = (personality: string) => {
    const current = config.personalities || [];
    if (current.includes(personality)) {
      const updated = current.filter(p => p !== personality);
      onConfigUpdate({ personalities: updated.length > 0 ? updated : null });
    } else {
      onConfigUpdate({ personalities: [...current, personality] });
    }
  };

  const handlePromptConfigToggle = (field: keyof PromptConfig) => {
    const current = config.prompt_config || DEFAULT_PROMPT_CONFIG;
    const updated = { ...current, [field]: !current[field] };
    onConfigUpdate({ prompt_config: updated });
  };

  // A/B Testing helpers
  const isAbTestingEnabled = config.control !== null;

  const handleToggleAbTesting = () => {
    if (isAbTestingEnabled) {
      // Disable: clear control and variants
      onConfigUpdate({ control: null, variants: null });
    } else {
      // Enable: create default control (uses experiment-level model/provider)
      onConfigUpdate({
        control: {
          label: 'Control',
          // model and provider are NOT set here - control uses experiment-level settings
        },
        variants: [],
      });
      setAbTestingExpanded(true);
    }
  };

  const handleControlUpdate = (field: keyof ControlConfig, value: string | boolean) => {
    if (!config.control) return;
    onConfigUpdate({
      control: { ...config.control, [field]: value },
    });
  };

  const handleAddVariant = () => {
    const variants = config.variants || [];
    const newVariant: VariantConfig = {
      label: `Variant ${variants.length + 1}`,
      model: '',
      provider: '',
    };
    onConfigUpdate({ variants: [...variants, newVariant] });
  };

  const handleVariantUpdate = (index: number, field: keyof VariantConfig, value: string | boolean | undefined) => {
    const variants = [...(config.variants || [])];
    variants[index] = { ...variants[index], [field]: value };
    onConfigUpdate({ variants });
  };

  const handleRemoveVariant = (index: number) => {
    const variants = [...(config.variants || [])];
    variants.splice(index, 1);
    onConfigUpdate({ variants });
  };

  const getTotalTournaments = () => {
    if (!isAbTestingEnabled) return config.num_tournaments;
    const numVariants = 1 + (config.variants?.length || 0);
    return config.num_tournaments * numVariants;
  };

  const getTotalHands = () => {
    return getTotalTournaments() * config.hands_per_tournament;
  };

  // Rough time estimate based on ~20 seconds per hand baseline
  // Factors: psychology/commentary add overhead, parallel tournaments reduce wall time
  const getTimeEstimate = () => {
    const totalHands = getTotalHands();
    const parallelism = config.parallel_tournaments || 1;

    // Base time per hand: ~20 seconds
    // Psychology adds ~5 seconds (emotional state generation)
    // Commentary adds ~5 seconds (LLM commentary generation)
    const hasPsychology = config.control?.enable_psychology ||
      config.variants?.some(v => v.enable_psychology);
    const hasCommentary = config.control?.enable_commentary ||
      config.variants?.some(v => v.enable_commentary);

    let secondsPerHand = 20;
    if (hasPsychology) secondsPerHand += 5;
    if (hasCommentary) secondsPerHand += 5;

    // Parallel execution reduces wall time
    const effectiveHands = Math.ceil(totalHands / parallelism);
    const totalSeconds = effectiveHands * secondsPerHand;

    // Format as human-readable duration
    if (totalSeconds < 60) {
      return `~${totalSeconds}s`;
    } else if (totalSeconds < 3600) {
      const minutes = Math.round(totalSeconds / 60);
      return `~${minutes}m`;
    } else {
      const hours = Math.floor(totalSeconds / 3600);
      const minutes = Math.round((totalSeconds % 3600) / 60);
      return minutes > 0 ? `~${hours}h ${minutes}m` : `~${hours}h`;
    }
  };

  const handleLaunch = async () => {
    if (!validation?.valid || launching) return;

    setLaunching(true);
    try {
      const response = await fetch(`${appConfig.API_URL}/api/experiments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          config,
          session_id: sessionId,  // Pass design chat session for history preservation
        }),
      });

      const data = await response.json();

      if (data.success) {
        onLaunch();
      } else {
        alert(`Failed to launch: ${data.error}`);
      }
    } catch (err) {
      alert('Failed to connect to server');
    } finally {
      setLaunching(false);
    }
  };

  const isConfigComplete = Boolean(config.name);
  const canLaunch = validation?.valid && isConfigComplete && !launching;

  return (
    <div className="config-preview">
      {/* Header */}
      <div className="config-preview__header">
        <div className="config-preview__header-left">
          <h4 className="config-preview__title">Experiment Config</h4>
          {/* Version Navigation */}
          {configVersions && configVersions.length > 1 && (
            <div className="config-preview__version-nav">
              <button
                className="config-preview__version-btn"
                onClick={() => onVersionChange?.(currentVersionIndex! - 1)}
                disabled={currentVersionIndex === undefined || currentVersionIndex <= 0}
                title="Previous version"
                type="button"
              >
                <ChevronLeft size={14} />
              </button>
              <span className="config-preview__version-label">
                v{(currentVersionIndex ?? 0) + 1}/{configVersions.length}
              </span>
              <button
                className="config-preview__version-btn"
                onClick={() => onVersionChange?.(currentVersionIndex! + 1)}
                disabled={currentVersionIndex === undefined || currentVersionIndex >= configVersions.length - 1}
                title="Next version"
                type="button"
              >
                <ChevronRight size={14} />
              </button>
            </div>
          )}
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
                  onChange={(e) => handleFieldChange('name', e.target.value)}
                  placeholder="experiment_name"
                />
              </label>

              <label className="config-preview__label">
                Description
                <textarea
                  className="config-preview__input config-preview__textarea"
                  rows={3}
                  value={config.description}
                  onChange={(e) => handleFieldChange('description', e.target.value)}
                  placeholder="What this experiment tests"
                />
              </label>

              <label className="config-preview__label">
                Hypothesis
                <textarea
                  className="config-preview__input config-preview__textarea"
                  rows={3}
                  value={config.hypothesis}
                  onChange={(e) => handleFieldChange('hypothesis', e.target.value)}
                  placeholder="Expected outcome"
                />
              </label>
            </div>

            {/* Tournament Settings */}
            <div className="config-preview__section">
              <h5 className="config-preview__section-title">Tournament Settings</h5>

              <div className="config-preview__tournament-grid">
                {/* Column 1: Game Structure */}
                <div className="config-preview__tournament-col">
                  <label className="config-preview__label config-preview__label--inline">
                    Tournaments
                    <input
                      type="number"
                      className="config-preview__input config-preview__input--small"
                      value={config.num_tournaments}
                      onChange={(e) => handleNumberChange('num_tournaments', e.target.value)}
                      onBlur={(e) => handleNumberBlur('num_tournaments', e.target.value)}
                      min={1}
                      max={20}
                    />
                  </label>

                  <label className="config-preview__label config-preview__label--inline">
                    Hands
                    <input
                      type="number"
                      className="config-preview__input config-preview__input--small"
                      value={config.hands_per_tournament}
                      onChange={(e) => handleNumberChange('hands_per_tournament', e.target.value)}
                      onBlur={(e) => handleNumberBlur('hands_per_tournament', e.target.value)}
                      min={5}
                      max={500}
                    />
                  </label>

                  <label className="config-preview__label config-preview__label--inline">
                    Players
                    <input
                      type="number"
                      className="config-preview__input config-preview__input--small"
                      value={config.num_players}
                      onChange={(e) => handleNumberChange('num_players', e.target.value)}
                      onBlur={(e) => handleNumberBlur('num_players', e.target.value)}
                      min={2}
                      max={8}
                    />
                  </label>
                </div>

                {/* Column 2: Chip Settings */}
                <div className="config-preview__tournament-col">
                  <label className="config-preview__label config-preview__label--inline">
                    Starting Stack
                    <input
                      type="number"
                      className="config-preview__input config-preview__input--small"
                      value={config.starting_stack}
                      onChange={(e) => handleNumberChange('starting_stack', e.target.value)}
                      onBlur={(e) => handleNumberBlur('starting_stack', e.target.value)}
                      min={1000}
                      max={100000}
                      step={1000}
                    />
                  </label>

                  <label className="config-preview__label config-preview__label--inline">
                    Big Blind
                    <input
                      type="number"
                      className="config-preview__input config-preview__input--small"
                      value={config.big_blind}
                      onChange={(e) => handleNumberChange('big_blind', e.target.value)}
                      onBlur={(e) => handleNumberBlur('big_blind', e.target.value)}
                      min={10}
                      max={1000}
                      step={10}
                    />
                  </label>

                  <label className="config-preview__toggle-label" title="When enabled, stacks reset on elimination ensuring exactly the configured number of hands. When disabled, tournament ends when one player wins all chips.">
                    <input
                      type="checkbox"
                      checked={config.reset_on_elimination ?? false}
                      onChange={(e) => handleFieldChange('reset_on_elimination', e.target.checked)}
                    />
                    Reset stacks on elimination
                  </label>
                </div>
              </div>

              <p className="config-preview__hint config-preview__hint--tournament">
                {config.reset_on_elimination
                  ? `Plays exactly ${config.hands_per_tournament} hands per tournament (stacks reset when someone is eliminated)`
                  : `Plays up to ${config.hands_per_tournament} hands per tournament (ends early if one player wins all chips)`}
              </p>
            </div>

            {/* Model Settings */}
            <div className="config-preview__section">
              <h5 className="config-preview__section-title">Model Settings</h5>

              <div className="config-preview__row">
                <label className="config-preview__label config-preview__label--inline">
                  Provider
                  <select
                    className="config-preview__select"
                    value={config.provider}
                    onChange={(e) => {
                      const newProvider = e.target.value;
                      // Update both provider and model (to the new provider's default)
                      onConfigUpdate({
                        provider: newProvider,
                        model: getDefaultModel(newProvider) || config.model,
                      });
                    }}
                    disabled={providersLoading}
                  >
                    {providersLoading ? (
                      <option value="">Loading...</option>
                    ) : (
                      providers.map(p => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))
                    )}
                  </select>
                </label>

                <label className="config-preview__label config-preview__label--inline">
                  Model
                  <select
                    className="config-preview__select"
                    value={config.model}
                    onChange={(e) => handleFieldChange('model', e.target.value)}
                    disabled={providersLoading}
                  >
                    {getModelsForProvider(config.provider).map(model => (
                      <option key={model} value={model}>{model}</option>
                    ))}
                  </select>
                </label>
              </div>
              {isAbTestingEnabled && (
                <p className="config-preview__hint">Control uses these settings. Add variants below to test different models.</p>
              )}
            </div>

            {/* A/B Testing (Collapsible) */}
            <div className="config-preview__section config-preview__section--collapsible">
              <button
                className="config-preview__section-toggle"
                onClick={() => setAbTestingExpanded(!abTestingExpanded)}
                type="button"
              >
                {abTestingExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                <FlaskConical size={16} />
                <h5 className="config-preview__section-title">A/B Testing</h5>
                <span className="config-preview__section-hint">
                  {isAbTestingEnabled ? `${1 + (config.variants?.length || 0)} variants` : 'Disabled'}
                </span>
              </button>

              {abTestingExpanded && (
                <div className="config-preview__ab-testing">
                  {/* Toggle */}
                  <label className="config-preview__toggle-label config-preview__toggle-label--primary">
                    <input
                      type="checkbox"
                      checked={isAbTestingEnabled}
                      onChange={handleToggleAbTesting}
                    />
                    Enable A/B Testing
                  </label>

                  {isAbTestingEnabled && (
                    <>
                      {/* Total tournaments calculation */}
                      <div className="config-preview__ab-info">
                        <span>
                          Total tournaments: <strong>{getTotalTournaments()}</strong>
                          {' '}({config.num_tournaments} per variant × {1 + (config.variants?.length || 0)} variants)
                        </span>
                      </div>

                      {/* Control Configuration */}
                      <div className="config-preview__variant-card config-preview__variant-card--control">
                        <div className="config-preview__variant-header">
                          <span className="config-preview__variant-badge config-preview__variant-badge--control">Control</span>
                        </div>
                        <div className="config-preview__variant-fields">
                          <label className="config-preview__label config-preview__label--inline">
                            Label
                            <input
                              type="text"
                              className="config-preview__input"
                              value={config.control?.label || ''}
                              onChange={(e) => handleControlUpdate('label', e.target.value)}
                              placeholder="Control"
                            />
                          </label>
                          <p className="config-preview__hint config-preview__hint--info">
                            Uses Model Settings above ({config.provider}/{config.model})
                          </p>
                          <div className="config-preview__row config-preview__row--toggles">
                            <label className="config-preview__toggle-label" title="Enable tilt + emotional state generation (~4 LLM calls/hand)">
                              <input
                                type="checkbox"
                                checked={config.control?.enable_psychology ?? false}
                                onChange={(e) => handleControlUpdate('enable_psychology', e.target.checked)}
                              />
                              Psychology
                            </label>
                            <label className="config-preview__toggle-label" title="Enable commentary generation (~4 LLM calls/hand)">
                              <input
                                type="checkbox"
                                checked={config.control?.enable_commentary ?? false}
                                onChange={(e) => handleControlUpdate('enable_commentary', e.target.checked)}
                              />
                              Commentary
                            </label>
                          </div>
                        </div>
                      </div>

                      {/* Variants */}
                      {config.variants?.map((variant, index) => (
                        <div key={index} className={`config-preview__variant-card config-preview__variant-card--color-${index % 5}`}>
                          <div className="config-preview__variant-header">
                            <span className={`config-preview__variant-badge config-preview__variant-badge--color-${index % 5}`}>Variant {index + 1}</span>
                            <button
                              type="button"
                              className="config-preview__variant-remove"
                              onClick={() => handleRemoveVariant(index)}
                              title="Remove variant"
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                          <div className="config-preview__variant-fields">
                            <label className="config-preview__label config-preview__label--inline">
                              Label
                              <input
                                type="text"
                                className="config-preview__input"
                                value={variant.label || ''}
                                onChange={(e) => handleVariantUpdate(index, 'label', e.target.value)}
                                placeholder={`Variant ${index + 1}`}
                              />
                            </label>
                            <label className="config-preview__label config-preview__label--inline">
                              Provider
                              <select
                                className="config-preview__select"
                                value={variant.provider || ''}
                                onChange={(e) => {
                                  const newProvider = e.target.value;
                                  if (newProvider === '') {
                                    // Inherit from experiment - clear both provider and model
                                    handleVariantUpdate(index, 'provider', '');
                                    handleVariantUpdate(index, 'model', '');
                                  } else {
                                    // Update provider and set default model
                                    const variants = [...(config.variants || [])];
                                    variants[index] = {
                                      ...variants[index],
                                      provider: newProvider,
                                      model: getDefaultModel(newProvider) || '',
                                    };
                                    onConfigUpdate({ variants });
                                  }
                                }}
                                disabled={providersLoading}
                              >
                                <option value="">Same as Control</option>
                                {providers.map(p => (
                                  <option key={p.id} value={p.id}>{p.name}</option>
                                ))}
                              </select>
                            </label>
                            <label className="config-preview__label config-preview__label--inline">
                              Model
                              <select
                                className="config-preview__select"
                                value={variant.model || ''}
                                onChange={(e) => handleVariantUpdate(index, 'model', e.target.value)}
                                disabled={providersLoading || !variant.provider}
                              >
                                <option value="">Same as Control</option>
                                {variant.provider && getModelsForProvider(variant.provider).map(model => (
                                  <option key={model} value={model}>{model}</option>
                                ))}
                              </select>
                            </label>
                            <div className="config-preview__row config-preview__row--toggles">
                              <label className="config-preview__toggle-label" title="Enable tilt + emotional state generation. Inherits from control if not set.">
                                <input
                                  type="checkbox"
                                  checked={variant.enable_psychology ?? config.control?.enable_psychology ?? false}
                                  onChange={(e) => handleVariantUpdate(index, 'enable_psychology', e.target.checked)}
                                />
                                Psychology
                              </label>
                              <label className="config-preview__toggle-label" title="Enable commentary generation. Inherits from control if not set.">
                                <input
                                  type="checkbox"
                                  checked={variant.enable_commentary ?? config.control?.enable_commentary ?? false}
                                  onChange={(e) => handleVariantUpdate(index, 'enable_commentary', e.target.checked)}
                                />
                                Commentary
                              </label>
                            </div>
                          </div>
                        </div>
                      ))}

                      {/* Add Variant Button */}
                      <button
                        type="button"
                        className="config-preview__add-variant-btn"
                        onClick={handleAddVariant}
                      >
                        <Plus size={14} />
                        Add Variant
                      </button>
                      <p className="config-preview__hint">
                        Variants can override model, provider, psychology, and commentary settings
                      </p>
                    </>
                  )}
                </div>
              )}
            </div>

            {/* Prompt Config (Collapsible) */}
            <div className="config-preview__section config-preview__section--collapsible">
              <button
                className="config-preview__section-toggle"
                onClick={() => setPromptConfigExpanded(!promptConfigExpanded)}
                type="button"
              >
                {promptConfigExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                <h5 className="config-preview__section-title">Prompt Config</h5>
                <span className="config-preview__section-hint">
                  {config.prompt_config ? 'Custom' : 'Default (all enabled)'}
                </span>
              </button>

              {promptConfigExpanded && (
                <div className="config-preview__prompt-toggles">
                  {Object.entries(PROMPT_CONFIG_LABELS).map(([key, label]) => {
                    if (key === 'memory_keep_exchanges') return null;
                    const field = key as keyof PromptConfig;
                    const value = config.prompt_config?.[field] ?? DEFAULT_PROMPT_CONFIG[field];
                    return (
                      <label key={key} className="config-preview__toggle-label">
                        <input
                          type="checkbox"
                          checked={value as boolean}
                          onChange={() => handlePromptConfigToggle(field)}
                        />
                        {label}
                      </label>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Advanced Settings (Collapsible) */}
            <div className="config-preview__section config-preview__section--collapsible">
              <button
                className="config-preview__section-toggle"
                onClick={() => setAdvancedExpanded(!advancedExpanded)}
                type="button"
              >
                {advancedExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                <Zap size={16} />
                <h5 className="config-preview__section-title">Advanced Settings</h5>
                <span className="config-preview__section-hint">
                  {config.parallel_tournaments && config.parallel_tournaments > 1 ? `${config.parallel_tournaments}x parallel` : 'Sequential'}
                </span>
              </button>

              {advancedExpanded && (
                <div className="config-preview__advanced">
                  {/* Deterministic Seeding */}
                  <div className="config-preview__seed-section">
                    <label className="config-preview__toggle-label" title="Enable deterministic seeding for reproducible experiments. Same seed = same player seating order and card shuffling across all tables. Essential for fair A/B comparisons.">
                      <input
                        type="checkbox"
                        checked={config.random_seed !== null}
                        onChange={(e) => {
                          if (e.target.checked) {
                            // Restore the saved seed when re-enabling
                            onConfigUpdate({ random_seed: savedSeed });
                          } else {
                            // Save the current seed before disabling
                            if (config.random_seed !== null) {
                              setSavedSeed(config.random_seed);
                            }
                            onConfigUpdate({ random_seed: null });
                          }
                        }}
                      />
                      Deterministic Seeding
                    </label>
                    {config.random_seed !== null && (
                      <div className="config-preview__seed-row">
                        <span className="config-preview__seed-label">Seed</span>
                        <div className="config-preview__seed-input">
                          <input
                            type="text"
                            className="config-preview__input config-preview__input--seed"
                            value={seedToWords(config.random_seed)}
                            onChange={(e) => {
                              const val = e.target.value.toLowerCase().trim();
                              // Try to parse as word seed
                              if (isWordSeed(val)) {
                                const numericSeed = wordsToSeed(val);
                                if (numericSeed !== null) {
                                  setSavedSeed(numericSeed);
                                  onConfigUpdate({ random_seed: numericSeed });
                                }
                              }
                              // Also accept numeric input
                              const numVal = parseInt(val);
                              if (!isNaN(numVal) && numVal > 0) {
                                setSavedSeed(numVal);
                                onConfigUpdate({ random_seed: numVal });
                              }
                            }}
                            placeholder="swift-tiger"
                          />
                          <button
                            type="button"
                            className="config-preview__regenerate-btn"
                            onClick={() => {
                              const newSeed = generateSeed();
                              setSavedSeed(newSeed);
                              onConfigUpdate({ random_seed: newSeed });
                            }}
                            title="Generate new seed"
                          >
                            <Shuffle size={14} />
                          </button>
                        </div>
                      </div>
                    )}
                    <p className="config-preview__hint">
                      {config.random_seed !== null
                        ? 'Same seed = same player seating & card order across tables (fair A/B comparison)'
                        : 'Random seating & deck order each tournament (more variance)'}
                    </p>
                  </div>

                  {/* Parallel Execution */}
                  <div className="config-preview__row">
                    <label className="config-preview__label config-preview__label--inline">
                      Parallel Tournaments
                      <input
                        type="number"
                        className="config-preview__input config-preview__input--small"
                        value={config.parallel_tournaments ?? 1}
                        onChange={(e) => handleNumberChange('parallel_tournaments' as keyof ExperimentConfig, e.target.value)}
                        onBlur={(e) => handleNumberBlur('parallel_tournaments' as keyof ExperimentConfig, e.target.value)}
                        min={1}
                        max={10}
                      />
                    </label>
                    <label className="config-preview__label config-preview__label--inline">
                      Stagger Delay (s)
                      <input
                        type="number"
                        className="config-preview__input config-preview__input--small"
                        value={config.stagger_start_delay ?? 0}
                        onChange={(e) => handleFloatChange('stagger_start_delay' as keyof ExperimentConfig, e.target.value)}
                        onBlur={(e) => handleFloatBlur('stagger_start_delay' as keyof ExperimentConfig, e.target.value)}
                        min={0}
                        max={60}
                        step={0.5}
                      />
                    </label>
                  </div>

                  {/* Tags */}
                  <div className="config-preview__tags-section">
                    <div className="config-preview__tags-input-row">
                      <span className="config-preview__tags-label">
                        <Tag size={14} />
                        Tags
                      </span>
                      <input
                        type="text"
                        className="config-preview__input"
                        value={tagInput}
                        onChange={(e) => setTagInput(e.target.value)}
                        onKeyDown={handleTagKeyDown}
                        placeholder="Add tag..."
                      />
                      <button
                        type="button"
                        className="config-preview__add-tag-btn"
                        onClick={handleAddTag}
                        disabled={!tagInput.trim()}
                      >
                        <Plus size={14} />
                      </button>
                    </div>
                    {config.tags.length > 0 && (
                      <div className="config-preview__tags-list">
                        {config.tags.map((tag) => (
                          <span key={tag} className="config-preview__tag">
                            {tag}
                            <button
                              type="button"
                              className="config-preview__tag-remove"
                              onClick={() => handleRemoveTag(tag)}
                            >
                              <X size={12} />
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Personalities */}
                  <div className="config-preview__personalities-section">
                    <label className="config-preview__label">
                      <Users size={14} style={{ display: 'inline', marginRight: '4px', verticalAlign: 'middle' }} />
                      Personalities
                      <span className="config-preview__label-hint">
                        {config.personalities ? `${config.personalities.length} selected` : 'Random'}
                      </span>
                    </label>
                    <div className="config-preview__personalities-grid">
                      {availablePersonalities.map((personality) => (
                        <label key={personality} className="config-preview__personality-item">
                          <input
                            type="checkbox"
                            checked={config.personalities?.includes(personality) ?? false}
                            onChange={() => handlePersonalityToggle(personality)}
                          />
                          <span>{personality}</span>
                        </label>
                      ))}
                    </div>
                    {config.personalities && config.personalities.length > 0 && (
                      <button
                        type="button"
                        className="config-preview__clear-personalities"
                        onClick={() => onConfigUpdate({ personalities: null })}
                      >
                        Clear Selection (use random)
                      </button>
                    )}
                  </div>

                  {/* Capture Prompts Toggle */}
                  <label className="config-preview__toggle-label">
                    <input
                      type="checkbox"
                      checked={config.capture_prompts}
                      onChange={(e) => handleFieldChange('capture_prompts', e.target.checked)}
                    />
                    Capture Prompts (for debugging)
                  </label>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="config-preview__json">
            <textarea
              className={`config-preview__json-editor ${jsonError ? 'config-preview__json-editor--error' : ''}`}
              value={jsonText}
              onChange={(e) => handleJsonChange(e.target.value)}
              spellCheck={false}
            />
            {jsonError && (
              <div className="config-preview__json-error">
                <AlertCircle size={14} />
                {jsonError}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Validation Messages */}
      {validation && (validation.errors.length > 0 || validation.warnings.length > 0) && (
        <div className="config-preview__validation">
          {validation.errors.map((error, i) => (
            <div key={i} className="config-preview__validation-error">
              <AlertCircle size={14} />
              {error}
            </div>
          ))}
          {validation.warnings.map((warning, i) => (
            <div key={i} className="config-preview__validation-warning">
              <AlertTriangle size={14} />
              {warning}
            </div>
          ))}
        </div>
      )}

      {/* Launch Button */}
      <div className="config-preview__footer">
        <div className="config-preview__estimate">
          <span className="config-preview__estimate-hands">
            {getTotalHands().toLocaleString()} hands
          </span>
          <span className="config-preview__estimate-time">
            {getTimeEstimate()}
          </span>
        </div>
        <button
          className="config-preview__launch-btn"
          onClick={handleLaunch}
          disabled={!canLaunch}
          type="button"
        >
          {launching ? (
            <>
              <Loader2 size={18} className="animate-spin" />
              Launching...
            </>
          ) : (
            <>
              <Play size={18} />
              Launch Experiment
            </>
          )}
        </button>
        {validating && (
          <span className="config-preview__validating">
            <Loader2 size={14} className="animate-spin" />
            Validating...
          </span>
        )}
      </div>
    </div>
  );
}

export default ConfigPreview;
