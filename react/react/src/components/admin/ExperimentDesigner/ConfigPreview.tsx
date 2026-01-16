import { useState, useEffect, useCallback } from 'react';
import { Play, Code, Settings, ChevronDown, ChevronRight, AlertCircle, AlertTriangle, Loader2 } from 'lucide-react';
import type { ExperimentConfig, PromptConfig } from './types';
import { DEFAULT_PROMPT_CONFIG } from './types';
import { config as appConfig } from '../../../config';

interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

interface ConfigPreviewProps {
  config: ExperimentConfig;
  onConfigUpdate: (updates: Partial<ExperimentConfig>) => void;
  onLaunch: () => void;
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

export function ConfigPreview({ config, onConfigUpdate, onLaunch }: ConfigPreviewProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('form');
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [promptConfigExpanded, setPromptConfigExpanded] = useState(false);
  const [personalities, setPersonalities] = useState<string[]>([]);

  // Fetch available personalities
  useEffect(() => {
    const fetchPersonalities = async () => {
      try {
        const response = await fetch(`${appConfig.API_URL}/api/experiments/personalities`);
        const data = await response.json();
        if (data.success) {
          setPersonalities(data.personalities);
        }
      } catch (err) {
        console.error('Failed to load personalities:', err);
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
        setValidation({ valid: false, errors: ['Failed to validate'], warnings: [] });
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
      setJsonError('Invalid JSON');
    }
  };

  const handleFieldChange = (field: keyof ExperimentConfig, value: unknown) => {
    onConfigUpdate({ [field]: value });
  };

  const handlePromptConfigToggle = (field: keyof PromptConfig) => {
    const current = config.prompt_config || DEFAULT_PROMPT_CONFIG;
    const updated = { ...current, [field]: !current[field] };
    onConfigUpdate({ prompt_config: updated });
  };

  const handleLaunch = async () => {
    if (!validation?.valid || launching) return;

    setLaunching(true);
    try {
      const response = await fetch(`${appConfig.API_URL}/api/experiments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config }),
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
        <h4 className="config-preview__title">Experiment Config</h4>
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
                <input
                  type="text"
                  className="config-preview__input"
                  value={config.description}
                  onChange={(e) => handleFieldChange('description', e.target.value)}
                  placeholder="What this experiment tests"
                />
              </label>

              <label className="config-preview__label">
                Hypothesis
                <input
                  type="text"
                  className="config-preview__input"
                  value={config.hypothesis}
                  onChange={(e) => handleFieldChange('hypothesis', e.target.value)}
                  placeholder="Expected outcome"
                />
              </label>
            </div>

            {/* Tournament Settings */}
            <div className="config-preview__section">
              <h5 className="config-preview__section-title">Tournament Settings</h5>

              <div className="config-preview__row">
                <label className="config-preview__label config-preview__label--inline">
                  Tournaments
                  <input
                    type="number"
                    className="config-preview__input config-preview__input--small"
                    value={config.num_tournaments}
                    onChange={(e) => handleFieldChange('num_tournaments', parseInt(e.target.value) || 1)}
                    min={1}
                    max={20}
                  />
                </label>

                <label className="config-preview__label config-preview__label--inline">
                  Max Hands
                  <input
                    type="number"
                    className="config-preview__input config-preview__input--small"
                    value={config.max_hands_per_tournament}
                    onChange={(e) => handleFieldChange('max_hands_per_tournament', parseInt(e.target.value) || 100)}
                    min={20}
                    max={500}
                  />
                </label>

                <label className="config-preview__label config-preview__label--inline">
                  Players
                  <input
                    type="number"
                    className="config-preview__input config-preview__input--small"
                    value={config.num_players}
                    onChange={(e) => handleFieldChange('num_players', parseInt(e.target.value) || 4)}
                    min={2}
                    max={8}
                  />
                </label>
              </div>

              <div className="config-preview__row">
                <label className="config-preview__label config-preview__label--inline">
                  Starting Stack
                  <input
                    type="number"
                    className="config-preview__input config-preview__input--small"
                    value={config.starting_stack}
                    onChange={(e) => handleFieldChange('starting_stack', parseInt(e.target.value) || 10000)}
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
                    onChange={(e) => handleFieldChange('big_blind', parseInt(e.target.value) || 100)}
                    min={10}
                    max={1000}
                    step={10}
                  />
                </label>
              </div>
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
                    onChange={(e) => handleFieldChange('provider', e.target.value)}
                  >
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                    <option value="groq">Groq</option>
                  </select>
                </label>

                <label className="config-preview__label config-preview__label--inline">
                  Model
                  <input
                    type="text"
                    className="config-preview__input"
                    value={config.model}
                    onChange={(e) => handleFieldChange('model', e.target.value)}
                    placeholder="gpt-5-nano"
                  />
                </label>
              </div>
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
      {validation && (
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
