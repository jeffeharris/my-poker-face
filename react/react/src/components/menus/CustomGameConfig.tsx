import { useState, useEffect, useCallback } from 'react';
import {
  Check, X, Search, Settings, Shuffle, Dices, Sparkles, ChevronDown,
  ArrowLeft, ArrowRight, UserPlus,
  FlaskConical, Clapperboard, Medal, Crown, Music, Laugh, Skull,
  Coins, Cpu, Users,
} from 'lucide-react';
import { logger } from '../../utils/logger';
import { config } from '../../config';
import { PageLayout, PageHeader, MenuBar } from '../shared';
import { useLLMProviders } from '../../hooks/useLLMProviders';
import type { OpponentLLMConfig, OpponentConfig } from '../../types/llm';
import { GAME_MODES } from '../../constants/gameModes';
import './CustomGameConfig.css';

// ─── Types ───────────────────────────────────────────────────────────

interface Personality {
  name: string;
  play_style: string;
  personality_traits: {
    bluff_tendency: number;
    aggression: number;
    chattiness: number;
    emoji_usage: number;
  };
}

interface LLMConfig {
  provider: string;
  model: string;
  reasoning_effort: string;
  starting_stack?: number;
  big_blind?: number;
  blind_growth?: number;
  blinds_increase?: number;
  max_blind?: number;
}

interface CustomGameConfigProps {
  onStartGame: (
    selectedPersonalities: Array<string | { name: string; llm_config: OpponentLLMConfig }>,
    llmConfig: LLMConfig,
    gameMode: string
  ) => void;
  onBack: () => void;
  isCreatingGame?: boolean;
}

// ─── Theme data ──────────────────────────────────────────────────────

const THEMES = [
  { id: 'science', name: 'Science Masters', icon: FlaskConical, description: 'Great minds think alike... or do they?' },
  { id: 'hollywood', name: 'Hollywood Legends', icon: Clapperboard, description: 'Lights, camera, all-in!' },
  { id: 'sports', name: 'Sports Champions', icon: Medal, description: 'Bring your A-game to the table' },
  { id: 'history', name: 'Historical Figures', icon: Crown, description: 'Making history one hand at a time' },
  { id: 'music', name: 'Music Icons', icon: Music, description: 'Feel the rhythm of the cards' },
  { id: 'comedy', name: 'Comedy Legends', icon: Laugh, description: 'No joke - these players are serious!' },
  { id: 'villains', name: 'Famous Villains', icon: Skull, description: 'Sometimes it pays to be bad' },
  { id: 'surprise', name: 'Surprise Me!', icon: Sparkles, description: 'A mysterious mix of personalities' },
];

// ─── Game presets ────────────────────────────────────────────────────

interface GamePreset {
  id: string;
  name: string;
  icon: string;
  desc: string;
  starting_stack: number;
  big_blind: number;
  blind_growth: number;
  blinds_increase: number;
  max_blind: number;
}

const GAME_PRESETS: GamePreset[] = [
  {
    id: 'quick', name: 'Quick & Dirty', icon: '⚡', desc: '1K stack, fast blinds. Games end quick.',
    starting_stack: 1000, big_blind: 50, blind_growth: 2, blinds_increase: 4, max_blind: 0,
  },
  {
    id: 'tournament', name: 'Tournament', icon: '🏆', desc: '10K stack, steady growth. Classic feel.',
    starting_stack: 10000, big_blind: 50, blind_growth: 1.5, blinds_increase: 6, max_blind: 0,
  },
  {
    id: 'deep', name: 'Deep Stack', icon: '💰', desc: '20K stack, slow blinds. Play the long game.',
    starting_stack: 20000, big_blind: 25, blind_growth: 1.25, blinds_increase: 10, max_blind: 0,
  },
];

// ─── Component ───────────────────────────────────────────────────────

export function CustomGameConfig({ onStartGame, onBack, isCreatingGame = false }: CustomGameConfigProps) {
  // Wizard step: 0 = opponents, 1 = settings, 2 = review
  const [step, setStep] = useState(0);

  // Step 1: Opponents
  const [allPersonalities, setAllPersonalities] = useState<Record<string, Personality>>({});
  const [loading, setLoading] = useState(true);
  const [playerCount, setPlayerCount] = useState(3);
  const [slots, setSlots] = useState<(string | null)[]>([null, null, null]);
  const [pickerSlotIndex, setPickerSlotIndex] = useState<number | null>(null);
  const [pickerSearch, setPickerSearch] = useState('');
  const [showThemes, setShowThemes] = useState(false);
  const [themeGenerating, setThemeGenerating] = useState(false);
  const [opponentConfigs, setOpponentConfigs] = useState<Record<string, OpponentConfig>>({});
  const [expandedConfigSlot, setExpandedConfigSlot] = useState<number | null>(null);

  // Step 2: Game settings
  const [selectedPreset, setSelectedPreset] = useState<string>('tournament');
  const [startingStack, setStartingStack] = useState(10000);
  const [bigBlind, setBigBlind] = useState(50);
  const [blindGrowth, setBlindGrowth] = useState(1.5);
  const [blindsIncrease, setBlindsIncrease] = useState(6);
  const [maxBlind, setMaxBlind] = useState(0);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [defaultGameMode, setDefaultGameMode] = useState('casual');

  // Model config
  const {
    providers, loading: providersLoading, getModelsForProvider,
    getDefaultModel, providerSupportsReasoning, formatModelLabel,
  } = useLLMProviders({ scope: 'user' });
  const [defaultProvider, setDefaultProvider] = useState('openai');
  const [defaultModel, setDefaultModel] = useState('gpt-5-nano');
  const [defaultReasoning, setDefaultReasoning] = useState('minimal');

  // Error state
  const [error, setError] = useState<string | null>(null);

  // ─── Data fetching ─────────────────────────────────────────────────

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${config.API_URL}/api/personalities`, { credentials: 'include' });
        const data = await res.json();
        if (data.success) setAllPersonalities(data.personalities);
      } catch (err) {
        logger.error('Failed to fetch personalities:', err);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (providers.length > 0) {
      const openai = providers.find(p => p.id === 'openai') || providers[0];
      setDefaultProvider(openai.id);
      setDefaultModel(openai.default_model);
    }
  }, [providers]);

  // ─── Player count management ───────────────────────────────────────

  const handlePlayerCountChange = useCallback((count: number) => {
    setPlayerCount(count);
    setSlots(prev => {
      if (count > prev.length) {
        return [...prev, ...Array(count - prev.length).fill(null)];
      }
      return prev.slice(0, count);
    });
  }, []);

  // ─── Slot filling ──────────────────────────────────────────────────

  const personalityNames = Object.keys(allPersonalities);
  const filledNames = slots.filter((s): s is string => s !== null);

  const fillRandomly = useCallback(() => {
    const available = personalityNames.filter(n => !filledNames.includes(n));
    const shuffled = [...available].sort(() => Math.random() - 0.5);
    setSlots(prev => prev.map(slot => {
      if (slot !== null) return slot;
      const pick = shuffled.shift();
      return pick ?? slot;
    }));
  }, [personalityNames, filledNames]);

  const shuffleAll = useCallback(() => {
    const available = personalityNames.sort(() => Math.random() - 0.5);
    setSlots(prev => prev.map((_, i) => available[i] ?? null));
    // Clear per-player configs since players changed
    setOpponentConfigs({});
  }, [personalityNames]);

  const handleThemeGenerate = async (themeId: string, themeName: string, desc: string) => {
    setShowThemes(false);
    setThemeGenerating(true);
    setError(null);
    setSlots(Array(playerCount).fill(null));
    setOpponentConfigs({});
    try {
      const res = await fetch(`${config.API_URL}/api/generate-theme`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: themeId, themeName, description: desc }),
      });
      if (res.status === 429) throw new Error('Rate limit hit. Wait a moment and try again.');
      if (!res.ok) throw new Error('Failed to generate theme.');
      const data = await res.json();
      const generated: string[] = data.personalities || [];
      // Adjust player count to match theme results (capped at 5)
      const count = Math.min(generated.length, 5);
      setPlayerCount(count);
      setSlots(generated.slice(0, count));
      setShowThemes(false);
      setOpponentConfigs({});
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Theme generation failed.');
    } finally {
      setThemeGenerating(false);
    }
  };

  const assignSlot = (slotIdx: number, name: string) => {
    setSlots(prev => prev.map((s, i) => (i === slotIdx ? name : s)));
    setPickerSlotIndex(null);
    setPickerSearch('');
  };

  const removeSlot = (slotIdx: number) => {
    const name = slots[slotIdx];
    setSlots(prev => prev.map((s, i) => (i === slotIdx ? null : s)));
    if (name && opponentConfigs[name]) {
      setOpponentConfigs(prev => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    }
    if (expandedConfigSlot === slotIdx) setExpandedConfigSlot(null);
  };

  // ─── Per-player LLM config ────────────────────────────────────────

  const handleOpponentConfigChange = (name: string, field: string, value: string) => {
    setOpponentConfigs(prev => {
      const current = prev[name] || { provider: defaultProvider, model: defaultModel, reasoning_effort: defaultReasoning };
      const updated = { ...current, [field]: value };
      // Cascade model reset on provider change
      if (field === 'provider') {
        const models = getModelsForProvider(value);
        if (!models.includes(updated.model)) {
          updated.model = getDefaultModel(value);
        }
        if (!providerSupportsReasoning(value)) {
          updated.reasoning_effort = 'minimal';
        }
      }
      return { ...prev, [name]: updated };
    });
  };

  const resetOpponentConfig = (name: string) => {
    setOpponentConfigs(prev => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
  };

  // ─── Default provider change ───────────────────────────────────────

  const handleDefaultProviderChange = (newProvider: string) => {
    setDefaultProvider(newProvider);
    const models = getModelsForProvider(newProvider);
    if (!models.includes(defaultModel)) {
      setDefaultModel(getDefaultModel(newProvider));
    }
    if (!providerSupportsReasoning(newProvider)) {
      setDefaultReasoning('minimal');
    }
  };

  // ─── Preset selection ──────────────────────────────────────────────

  const applyPreset = (preset: GamePreset) => {
    setSelectedPreset(preset.id);
    setStartingStack(preset.starting_stack);
    setBigBlind(preset.big_blind);
    setBlindGrowth(preset.blind_growth);
    setBlindsIncrease(preset.blinds_increase);
    setMaxBlind(preset.max_blind);
  };

  const handleSettingChange = (setter: (v: number) => void, value: number) => {
    setter(value);
    setSelectedPreset('custom');
  };

  // ─── Start game ────────────────────────────────────────────────────

  const handleStartGame = () => {
    const filled = slots.filter((s): s is string => s !== null);
    if (filled.length === 0) return;

    const personalities = filled.map(name => {
      const customConfig = opponentConfigs[name];
      if (customConfig) {
        const { game_mode, ...llm_config } = customConfig;
        const entry: { name: string; llm_config: OpponentLLMConfig; game_mode?: string } = { name, llm_config };
        if (game_mode) entry.game_mode = game_mode;
        return entry;
      }
      return name;
    });

    onStartGame(personalities, {
      provider: defaultProvider,
      model: defaultModel,
      reasoning_effort: defaultReasoning,
      starting_stack: startingStack,
      big_blind: bigBlind,
      blind_growth: blindGrowth,
      blinds_increase: blindsIncrease,
      max_blind: maxBlind,
    }, defaultGameMode);
  };

  // ─── Navigation ────────────────────────────────────────────────────

  const filledCount = slots.filter(s => s !== null).length;
  const canProceedStep0 = filledCount > 0;

  const goNext = () => setStep(s => Math.min(s + 1, 2));
  const goBack = () => setStep(s => Math.max(s - 1, 0));
  const goToStep = (s: number) => {
    // Only allow jumping to completed or current steps
    if (s <= step || (s === 1 && canProceedStep0) || (s === 2 && canProceedStep0)) {
      setStep(s);
    }
  };

  // ─── Step indicator ────────────────────────────────────────────────

  const STEP_LABELS = ['Opponents', 'Settings', 'Review'];

  const renderStepIndicator = () => (
    <nav className="wizard-steps" aria-label="Setup progress">
      {STEP_LABELS.map((label, i) => {
        const isCompleted = i < step;
        const isActive = i === step;
        const className = `wizard-step ${isActive ? 'wizard-step--active' : ''} ${isCompleted ? 'wizard-step--completed' : ''}`;
        return (
          <div key={label} style={{ display: 'flex', alignItems: 'center' }}>
            <button className={className} onClick={() => goToStep(i)} aria-current={isActive ? 'step' : undefined}>
              <span className="wizard-step__circle">
                {isCompleted ? <Check size={16} /> : i + 1}
              </span>
              <span className="wizard-step__label">{label}</span>
            </button>
            {i < STEP_LABELS.length - 1 && (
              <div className={`wizard-step__connector ${isCompleted ? 'wizard-step__connector--completed' : ''}`} />
            )}
          </div>
        );
      })}
    </nav>
  );

  // ─── Trait bar helper ──────────────────────────────────────────────

  const TraitBar = ({ label, value }: { label: string; value: number }) => (
    <div className="player-card__trait">
      <span className="player-card__trait-label">{label}</span>
      <div className="player-card__trait-bar">
        <div className="player-card__trait-fill" style={{ width: `${value * 100}%` }} />
      </div>
    </div>
  );

  // ─── Personality picker ────────────────────────────────────────────

  const renderPicker = () => {
    if (pickerSlotIndex === null) return null;

    const filtered = Object.entries(allPersonalities).filter(([name]) =>
      name.toLowerCase().includes(pickerSearch.toLowerCase())
    );

    return (
      <>
        <div className="personality-picker-backdrop" onClick={() => { setPickerSlotIndex(null); setPickerSearch(''); }} />
        <div className="personality-picker">
          <div className="personality-picker__handle">
            <div className="personality-picker__handle-bar" />
          </div>
          <div className="personality-picker__header">
            <h3 className="personality-picker__title">Choose Personality</h3>
            <button className="personality-picker__close" onClick={() => { setPickerSlotIndex(null); setPickerSearch(''); }}>
              <X size={20} />
            </button>
          </div>
          <div className="personality-picker__search">
            <Search size={16} className="personality-picker__search-icon" />
            <input
              className="personality-picker__search-input"
              type="text"
              placeholder="Search personalities..."
              value={pickerSearch}
              onChange={e => setPickerSearch(e.target.value)}
              autoFocus
            />
          </div>
          <div className="personality-picker__list">
            {filtered.length === 0 ? (
              <div className="personality-picker__empty">No matches found</div>
            ) : (
              filtered.map(([name, p]) => {
                const taken = filledNames.includes(name);
                return (
                  <button
                    key={name}
                    className={`personality-picker__item ${taken ? 'personality-picker__item--taken' : ''}`}
                    onClick={() => !taken && assignSlot(pickerSlotIndex, name)}
                    disabled={taken}
                  >
                    <div className="personality-picker__item-avatar">
                      {name[0]}
                    </div>
                    <div className="personality-picker__item-info">
                      <div className="personality-picker__item-name">{name}</div>
                      <div className="personality-picker__item-style">{p.play_style}</div>
                    </div>
                    {taken && <span style={{ fontSize: '10px', color: 'var(--color-text-disabled)' }}>IN USE</span>}
                  </button>
                );
              })
            )}
          </div>
        </div>
      </>
    );
  };

  // ─── Step 1: Choose Opponents ──────────────────────────────────────

  const renderStep0 = () => (
    <div className="wizard-step-panel" key="step-0">
      {/* Player count */}
      <div className="player-count">
        <span className="player-count__label">Number of Opponents</span>
        <div className="player-count__buttons">
          {[1, 2, 3, 4, 5].map(n => (
            <button
              key={n}
              className={`player-count__btn ${playerCount === n ? 'player-count__btn--selected' : ''}`}
              onClick={() => handlePlayerCountChange(n)}
            >
              {n}
            </button>
          ))}
        </div>
      </div>

      {/* Fill action buttons */}
      <div className="fill-actions">
        <div className="fill-actions__row">
          <button className="fill-btn fill-btn--random" onClick={fillRandomly} disabled={loading || filledCount === playerCount}>
            <Dices size={16} /> Fill Randomly
          </button>
          <button className="fill-btn fill-btn--theme" onClick={() => setShowThemes(true)} disabled={themeGenerating}>
            <Sparkles size={16} /> Use Theme
          </button>
        </div>
        {filledCount > 0 && (
          <button className="fill-btn fill-btn--shuffle" onClick={shuffleAll} disabled={loading}>
            <Shuffle size={16} /> Shuffle All
          </button>
        )}
      </div>

      {/* Theme picker modal */}
      {showThemes && (
        <div className="theme-modal-backdrop" onClick={() => setShowThemes(false)}>
          <div className="theme-modal" onClick={e => e.stopPropagation()}>
            <div className="theme-modal__header">
              <h3 className="theme-modal__title">
                <Sparkles size={18} /> Choose a Theme
              </h3>
              <button className="theme-modal__close" onClick={() => setShowThemes(false)}>✕</button>
            </div>
            <div className="theme-modal__grid">
              {THEMES.map(t => (
                <button
                  key={t.id}
                  className="theme-modal__card"
                  onClick={() => handleThemeGenerate(t.id, t.name, t.description)}
                  disabled={themeGenerating}
                >
                  <t.icon size={28} className="theme-modal__icon" />
                  <span className="theme-modal__name">{t.name}</span>
                  <span className="theme-modal__desc">{t.description}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Error */}
      {error && <div className="wizard-error">{error}</div>}

      {/* Loading state */}
      {themeGenerating && (
        <div className="wizard-loading">
          <div className="wizard-loading__spinner" />
          <div>Contacting agents for availability...</div>
        </div>
      )}

      {/* Player slots */}
      {loading ? (
        <div className="wizard-loading">
          <div className="wizard-loading__spinner" />
          <div>Loading personalities...</div>
        </div>
      ) : (
        <div className="player-slots">
          {slots.map((slotName, idx) => {
            if (slotName === null) {
              return (
                <button key={`empty-${idx}`} className="player-slot--empty" onClick={() => setPickerSlotIndex(idx)}>
                  <UserPlus size={24} className="player-slot__empty-icon" />
                  <span className="player-slot__empty-label">Empty Seat</span>
                  <span className="player-slot__empty-hint">Tap to assign</span>
                </button>
              );
            }

            const p = allPersonalities[slotName];
            if (!p) return null;
            const hasCustomConfig = !!opponentConfigs[slotName];
            const isConfigExpanded = expandedConfigSlot === idx;

            const avatarPath = `/api/avatar/${encodeURIComponent(slotName)}/confident/full`;
            const avatarUrl = `${config.API_URL}${avatarPath}`;

            return (
              <div
                key={slotName}
                className="player-card"
                style={{ backgroundImage: `url(${avatarUrl})` }}
              >
                <div className="player-card__header">
                  <span className="player-card__name">{slotName}</span>
                  <div className="player-card__actions">
                    <button
                      className={`player-card__action-btn player-card__action-btn--settings ${isConfigExpanded ? 'active' : ''}`}
                      onClick={() => setExpandedConfigSlot(isConfigExpanded ? null : idx)}
                      title="AI model settings"
                    >
                      <Settings size={14} />
                    </button>
                    <button
                      className="player-card__action-btn player-card__action-btn--remove"
                      onClick={() => removeSlot(idx)}
                      title="Remove"
                    >
                      <X size={14} />
                    </button>
                  </div>
                </div>

                <p className="player-card__style">{p.play_style}</p>

                <div className="player-card__traits">
                  <TraitBar label="Bluff" value={p.personality_traits.bluff_tendency} />
                  <TraitBar label="Aggro" value={p.personality_traits.aggression} />
                </div>

                {/* Inline LLM config */}
                {isConfigExpanded && (
                  <div className="player-card__config">
                    <div className="player-card__config-row">
                      <span className="player-card__config-label">Provider</span>
                      <select
                        className="player-card__config-select"
                        value={opponentConfigs[slotName]?.provider || defaultProvider}
                        onChange={e => handleOpponentConfigChange(slotName, 'provider', e.target.value)}
                        disabled={providersLoading}
                      >
                        {providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                      </select>

                      <span className="player-card__config-label">Model</span>
                      <select
                        className="player-card__config-select"
                        value={opponentConfigs[slotName]?.model || defaultModel}
                        onChange={e => handleOpponentConfigChange(slotName, 'model', e.target.value)}
                        disabled={providersLoading}
                      >
                        {getModelsForProvider(opponentConfigs[slotName]?.provider || defaultProvider).map(m => (
                          <option key={m} value={m}>{formatModelLabel(opponentConfigs[slotName]?.provider || defaultProvider, m)}</option>
                        ))}
                      </select>

                      {providerSupportsReasoning(opponentConfigs[slotName]?.provider || defaultProvider) && (
                        <>
                          <span className="player-card__config-label">Reasoning</span>
                          <select
                            className="player-card__config-select"
                            value={opponentConfigs[slotName]?.reasoning_effort || defaultReasoning}
                            onChange={e => handleOpponentConfigChange(slotName, 'reasoning_effort', e.target.value)}
                          >
                            {['minimal', 'low'].map(l => (
                              <option key={l} value={l}>{l.charAt(0).toUpperCase() + l.slice(1)}</option>
                            ))}
                          </select>
                        </>
                      )}

                      {hasCustomConfig && (
                        <button className="player-card__config-reset" onClick={() => resetOpponentConfig(slotName)}>
                          Reset to Default
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  // ─── Step 2: Game Settings ─────────────────────────────────────────

  const stackOptions = [1000, 2500, 5000, 10000, 20000];
  const blindOptions = [10, 25, 50, 100, 200];
  const blindGrowthOptions = [1.25, 1.5, 2];
  const blindsIncreaseOptions = [4, 6, 8, 10];
  const maxBlindOptions = [200, 500, 1000, 2000, 5000, 0];

  const renderStep1 = () => (
    <div className="wizard-step-panel" key="step-1">
      {/* Preset cards */}
      <div className="presets-section">
        <p className="presets-section__label">Choose a Starting Point</p>
        <div className="presets-grid">
          {GAME_PRESETS.map(preset => (
            <button
              key={preset.id}
              className={`preset-card ${selectedPreset === preset.id ? 'preset-card--selected' : ''}`}
              onClick={() => applyPreset(preset)}
            >
              {selectedPreset === 'custom' && <span className="preset-card__custom-badge">Custom</span>}
              <div className="preset-card__icon">{preset.icon}</div>
              <div className="preset-card__name">{preset.name}</div>
              <div className="preset-card__desc">{preset.desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Advanced toggle */}
      <button className="advanced-toggle" onClick={() => setShowAdvanced(!showAdvanced)}>
        <Settings size={16} />
        Advanced Settings
        <ChevronDown size={16} className={`advanced-toggle__chevron ${showAdvanced ? 'advanced-toggle__chevron--open' : ''}`} />
      </button>

      {/* Advanced panel */}
      {showAdvanced && (
        <div className="advanced-panel">
          {/* Game settings */}
          <div className="settings-section">
            <h4 className="settings-section__title">
              <Coins size={16} className="settings-section__title-icon" />
              Game Settings
            </h4>
            <div className="settings-table">
              <span className="setting-label">Starting Stack</span>
              <select className="setting-select" value={startingStack} onChange={e => handleSettingChange(setStartingStack, parseInt(e.target.value))}>
                {stackOptions.map(v => <option key={v} value={v}>{v.toLocaleString()}</option>)}
              </select>

              <span className="setting-label">Big Blind</span>
              <select className="setting-select" value={bigBlind} onChange={e => handleSettingChange(setBigBlind, parseInt(e.target.value))}>
                {blindOptions.map(v => <option key={v} value={v}>{v}</option>)}
              </select>

              <span className="setting-label">Blinds Increase</span>
              <select className="setting-select" value={blindsIncrease} onChange={e => handleSettingChange(setBlindsIncrease, parseInt(e.target.value))}>
                {blindsIncreaseOptions.map(v => <option key={v} value={v}>Every {v} hands</option>)}
              </select>

              <span className="setting-label">Blind Growth</span>
              <select className="setting-select" value={blindGrowth} onChange={e => handleSettingChange(setBlindGrowth, parseFloat(e.target.value))}>
                {blindGrowthOptions.map(v => <option key={v} value={v}>{v}x</option>)}
              </select>

              <span className="setting-label">Blind Cap</span>
              <select className="setting-select" value={maxBlind} onChange={e => handleSettingChange(setMaxBlind, parseInt(e.target.value))}>
                {maxBlindOptions.map(v => <option key={v} value={v}>{v === 0 ? 'No cap' : v.toLocaleString()}</option>)}
              </select>

              {startingStack < bigBlind * 10 && (
                <>
                  <span className="setting-label setting-label--warn">Note</span>
                  <span className="setting-warn">Short stack — less than 10× big blind. Games may end quickly.</span>
                </>
              )}

              <span className="setting-label">Game Mode</span>
              <select className="setting-select" value={defaultGameMode} onChange={e => setDefaultGameMode(e.target.value)}>
                {GAME_MODES.map(gm => <option key={gm.value} value={gm.value}>{gm.label} — {gm.description}</option>)}
              </select>
            </div>
          </div>

          {/* Model settings */}
          <div className="settings-section">
            <h4 className="settings-section__title">
              <Cpu size={16} className="settings-section__title-icon" />
              Default Model
            </h4>
            <div className="settings-table">
              <span className="setting-label">Provider</span>
              <select
                className="setting-select"
                value={defaultProvider}
                onChange={e => handleDefaultProviderChange(e.target.value)}
                disabled={providersLoading}
              >
                {providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>

              <span className="setting-label">Model</span>
              <select
                className="setting-select"
                value={defaultModel}
                onChange={e => setDefaultModel(e.target.value)}
                disabled={providersLoading}
              >
                {getModelsForProvider(defaultProvider).map(m => (
                  <option key={m} value={m}>{formatModelLabel(defaultProvider, m)}</option>
                ))}
              </select>

              {providerSupportsReasoning(defaultProvider) && (
                <>
                  <span className="setting-label">Reasoning</span>
                  <select className="setting-select" value={defaultReasoning} onChange={e => setDefaultReasoning(e.target.value)}>
                    {['minimal', 'low'].map(l => (
                      <option key={l} value={l}>{l.charAt(0).toUpperCase() + l.slice(1)}</option>
                    ))}
                  </select>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );

  // ─── Step 3: Review & Start ────────────────────────────────────────

  const activePreset = GAME_PRESETS.find(p => p.id === selectedPreset);

  const renderStep2 = () => (
    <div className="wizard-step-panel" key="step-2">
      {/* Opponents review */}
      <div className="review-section">
        <div className="review-section__header">
          <h4 className="review-section__title">
            <Users size={16} /> Opponents ({filledCount})
          </h4>
          <button className="review-section__edit" onClick={() => setStep(0)}>Edit</button>
        </div>
        <div className="review-players">
          {slots.filter((s): s is string => s !== null).map(name => (
            <div key={name} className="review-player">
              <div className="review-player__avatar">{name[0]}</div>
              <span className="review-player__name">{name}</span>
              {opponentConfigs[name] && (
                <Settings size={12} className="review-player__custom" />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Settings review */}
      <div className="review-section">
        <div className="review-section__header">
          <h4 className="review-section__title">
            <Coins size={16} /> Game Settings
            {activePreset && <span style={{ fontSize: '12px', color: 'var(--color-text-muted)', fontWeight: 400 }}>({activePreset.name})</span>}
          </h4>
          <button className="review-section__edit" onClick={() => setStep(1)}>Edit</button>
        </div>
        <div className="review-settings-grid">
          <div className="review-setting">
            <div className="review-setting__label">Stack</div>
            <div className="review-setting__value">{startingStack.toLocaleString()}</div>
          </div>
          <div className="review-setting">
            <div className="review-setting__label">Big Blind</div>
            <div className="review-setting__value">{bigBlind}</div>
          </div>
          <div className="review-setting">
            <div className="review-setting__label">Growth</div>
            <div className="review-setting__value">{blindGrowth}x</div>
          </div>
          <div className="review-setting">
            <div className="review-setting__label">Increase</div>
            <div className="review-setting__value">Every {blindsIncrease}h</div>
          </div>
          <div className="review-setting">
            <div className="review-setting__label">Mode</div>
            <div className="review-setting__value" style={{ textTransform: 'capitalize' }}>{defaultGameMode}</div>
          </div>
        </div>
      </div>

      {/* Model review */}
      <div className="review-section">
        <div className="review-section__header">
          <h4 className="review-section__title">
            <Cpu size={16} /> AI Model
          </h4>
          <button className="review-section__edit" onClick={() => { setStep(1); setShowAdvanced(true); }}>Edit</button>
        </div>
        <div className="review-settings-grid">
          <div className="review-setting">
            <div className="review-setting__label">Provider</div>
            <div className="review-setting__value" style={{ textTransform: 'capitalize' }}>{defaultProvider}</div>
          </div>
          <div className="review-setting">
            <div className="review-setting__label">Model</div>
            <div className="review-setting__value" style={{ fontSize: 'var(--font-size-sm)' }}>{defaultModel}</div>
          </div>
        </div>
      </div>

      {/* Start button */}
      <div className="start-game-area">
        <button
          className="start-button"
          onClick={handleStartGame}
          disabled={filledCount === 0 || isCreatingGame}
        >
          {isCreatingGame ? 'Creating Game...' : `Deal Me In`}
        </button>
      </div>
    </div>
  );

  // ─── Render ────────────────────────────────────────────────────────

  return (
    <>
      <MenuBar onBack={onBack} centerContent={renderStepIndicator()} showUserInfo onMainMenu={onBack} />
      <PageLayout variant="top" glowColor="sapphire" maxWidth="lg" hasMenuBar>

        <div className="wizard-content">
          {step === 0 && renderStep0()}
          {step === 1 && renderStep1()}
          {step === 2 && renderStep2()}
        </div>
      </PageLayout>

      {/* Navigation bar (fixed bottom - not shown on review step which has its own Start button) */}
      {step < 2 && (
        <div className="wizard-nav">
          {step > 0 ? (
            <button className="wizard-nav__btn wizard-nav__btn--back" onClick={goBack}>
              <ArrowLeft size={16} /> Back
            </button>
          ) : (
            <div className="wizard-nav__spacer" />
          )}
          <button
            className="wizard-nav__btn wizard-nav__btn--next"
            onClick={goNext}
            disabled={step === 0 && !canProceedStep0}
          >
            Next <ArrowRight size={16} />
          </button>
        </div>
      )}

      {renderPicker()}
    </>
  );
}
