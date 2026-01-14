import { useState, useEffect } from 'react';
import { Search, Check, Settings } from 'lucide-react';
import { config } from '../../config';
import { PageLayout, PageHeader } from '../shared';
import { OpponentConfigScreen } from './OpponentConfigScreen';
import './CustomGameConfig.css';

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

interface ProviderInfo {
  id: string;
  name: string;
  models: string[];
  default_model: string;
  capabilities: {
    supports_reasoning: boolean;
    supports_json_mode: boolean;
    supports_image_generation: boolean;
  };
}

interface OpponentLLMConfig {
  provider: string;
  model: string;
  reasoning_effort?: string;
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
    llmConfig: LLMConfig
  ) => void;
  onBack: () => void;
}

export function CustomGameConfig({ onStartGame, onBack }: CustomGameConfigProps) {
  const [personalities, setPersonalities] = useState<{ [key: string]: Personality }>({});
  const [selectedPersonalities, setSelectedPersonalities] = useState<string[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [loading, setLoading] = useState(true);

  // Provider and model configuration state
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [providersLoading, setProvidersLoading] = useState(true);
  const [defaultProvider, setDefaultProvider] = useState('openai');
  const [defaultModel, setDefaultModel] = useState('gpt-5-nano');
  const [reasoningLevels] = useState(['minimal', 'low']);
  const [defaultReasoning, setDefaultReasoning] = useState('minimal');

  // Per-opponent LLM configuration
  const [opponentConfigs, setOpponentConfigs] = useState<Record<string, OpponentLLMConfig>>({});
  const [showConfigScreen, setShowConfigScreen] = useState(false);

  // Game configuration state
  const [stackOptions] = useState([1000, 2500, 5000, 10000, 20000]);
  const [blindOptions] = useState([10, 25, 50, 100, 200]);
  const [startingStack, setStartingStack] = useState(10000);
  const [bigBlind, setBigBlind] = useState(50);

  // Blind escalation settings
  const [blindGrowthOptions] = useState([1.25, 1.5, 2]);
  const [blindsIncreaseOptions] = useState([4, 6, 8, 10]);
  const [maxBlindOptions] = useState([200, 500, 1000, 2000, 5000, 0]); // 0 = no limit
  const [blindGrowth, setBlindGrowth] = useState(1.5);
  const [blindsIncrease, setBlindsIncrease] = useState(6);
  const [maxBlind, setMaxBlind] = useState(0); // no limit by default

  useEffect(() => {
    fetchPersonalities();
    fetchProviders();
  }, []);

  const fetchPersonalities = async () => {
    try {
      const response = await fetch(`${config.API_URL}/api/personalities`, { credentials: 'include' });
      const data = await response.json();
      if (data.success) {
        setPersonalities(data.personalities);
      }
    } catch (error) {
      console.error('Failed to fetch personalities:', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchProviders = async () => {
    setProvidersLoading(true);
    try {
      const response = await fetch(`${config.API_URL}/api/llm-providers`, { credentials: 'include' });
      const data = await response.json();
      if (data.providers?.length > 0) {
        setProviders(data.providers);
        const defaultProv = data.providers.find((p: ProviderInfo) => p.id === 'openai') || data.providers[0];
        setDefaultProvider(defaultProv.id);
        setDefaultModel(defaultProv.default_model);
      }
    } catch (err) {
      console.debug('Provider fetch failed, using OpenAI fallback:', err);
      // Fallback to OpenAI only
      setProviders([{
        id: 'openai',
        name: 'OpenAI',
        models: ['gpt-5-nano', 'gpt-5-mini', 'gpt-5'],
        default_model: 'gpt-5-nano',
        capabilities: { supports_reasoning: true, supports_json_mode: true, supports_image_generation: true }
      }]);
    } finally {
      setProvidersLoading(false);
    }
  };

  // Helper functions for provider management
  const getModelsForProvider = (providerId: string): string[] => {
    const provider = providers.find(p => p.id === providerId);
    return provider?.models || [];
  };

  const providerSupportsReasoning = (providerId: string): boolean => {
    const provider = providers.find(p => p.id === providerId);
    return provider?.capabilities?.supports_reasoning ?? false;
  };

  const handleDefaultProviderChange = (newProvider: string) => {
    setDefaultProvider(newProvider);
    const provider = providers.find(p => p.id === newProvider);
    if (provider) {
      // Cascade: reset model if current not available in new provider
      if (!provider.models.includes(defaultModel)) {
        setDefaultModel(provider.default_model);
      }
      // Reset reasoning if provider doesn't support it
      if (!provider.capabilities?.supports_reasoning) {
        setDefaultReasoning('minimal');
      }
    }
  };

  const handleOpponentConfigChange = (name: string, newConfig: OpponentLLMConfig | null) => {
    setOpponentConfigs(prev => {
      const next = { ...prev };
      if (newConfig === null) {
        delete next[name];
      } else {
        // Validate model is available for provider
        const provider = providers.find(p => p.id === newConfig.provider);
        if (provider && !provider.models.includes(newConfig.model)) {
          newConfig.model = provider.default_model;
        }
        next[name] = newConfig;
      }
      return next;
    });
  };

  const customConfigCount = Object.keys(opponentConfigs).filter(
    name => selectedPersonalities.includes(name)
  ).length;

  const togglePersonality = (name: string) => {
    if (selectedPersonalities.includes(name)) {
      setSelectedPersonalities(prev => prev.filter(p => p !== name));
    } else if (selectedPersonalities.length < 5) {
      setSelectedPersonalities(prev => [...prev, name]);
    }
  };

  const filteredPersonalities = Object.entries(personalities).filter(([name]) =>
    name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleStartGame = () => {
    if (selectedPersonalities.length > 0) {
      // Build personalities array with optional llm_config overrides
      const personalities = selectedPersonalities.map(name => {
        const customConfig = opponentConfigs[name];
        if (customConfig) {
          return { name, llm_config: customConfig };
        }
        return name;
      });

      const llmConfig: LLMConfig = {
        provider: defaultProvider,
        model: defaultModel,
        reasoning_effort: defaultReasoning,
        starting_stack: startingStack,
        big_blind: bigBlind,
        blind_growth: blindGrowth,
        blinds_increase: blindsIncrease,
        max_blind: maxBlind
      };
      onStartGame(personalities, llmConfig);
    }
  };

  const getTraitBar = (value: number) => {
    const percentage = value * 100;
    return (
      <div className="cgc-trait-bar">
        <div 
          className="cgc-trait-fill" 
          style={{ width: `${percentage}%` }}
        />
      </div>
    );
  };

  // Show opponent config screen if toggled
  if (showConfigScreen) {
    return (
      <OpponentConfigScreen
        selectedOpponents={selectedPersonalities}
        providers={providers}
        providersLoading={providersLoading}
        defaultConfig={{
          provider: defaultProvider,
          model: defaultModel,
          reasoning_effort: defaultReasoning,
        }}
        opponentConfigs={opponentConfigs}
        onConfigChange={handleOpponentConfigChange}
        onBack={() => setShowConfigScreen(false)}
      />
    );
  }

  return (
    <PageLayout variant="top" glowColor="sapphire" maxWidth="xl">
      <PageHeader
        title="Custom Game Setup"
        subtitle="Choose your opponents (up to 5)"
        onBack={onBack}
        titleVariant="primary"
      />

        <div className="config-section">
          <h3>Game Settings</h3>
          <div className="settings-table">
            <span className="setting-label">Starting Stack</span>
            <select
              className="setting-select"
              value={startingStack}
              onChange={(e) => setStartingStack(parseInt(e.target.value))}
            >
              {stackOptions.map(stack => (
                <option key={stack} value={stack}>{stack.toLocaleString()}</option>
              ))}
            </select>

            <span className="setting-label">Big Blind</span>
            <select
              className="setting-select"
              value={bigBlind}
              onChange={(e) => setBigBlind(parseInt(e.target.value))}
            >
              {blindOptions.map(blind => (
                <option key={blind} value={blind}>{blind}</option>
              ))}
            </select>

            <span className="setting-label">Blinds Increase</span>
            <select
              className="setting-select"
              value={blindsIncrease}
              onChange={(e) => setBlindsIncrease(parseInt(e.target.value))}
            >
              {blindsIncreaseOptions.map(hands => (
                <option key={hands} value={hands}>Every {hands} hands</option>
              ))}
            </select>

            <span className="setting-label">Blind Growth</span>
            <select
              className="setting-select"
              value={blindGrowth}
              onChange={(e) => setBlindGrowth(parseFloat(e.target.value))}
            >
              {blindGrowthOptions.map(rate => (
                <option key={rate} value={rate}>{rate}x</option>
              ))}
            </select>

            <span className="setting-label">Blind Cap</span>
            <select
              className="setting-select"
              value={maxBlind}
              onChange={(e) => setMaxBlind(parseInt(e.target.value))}
            >
              {maxBlindOptions.map(cap => (
                <option key={cap} value={cap}>{cap === 0 ? 'No cap' : cap.toLocaleString()}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="config-section">
          <h3>Model Settings</h3>
          <div className="settings-table">
            <span className="setting-label">Provider</span>
            <select
              className="setting-select"
              value={defaultProvider}
              onChange={(e) => handleDefaultProviderChange(e.target.value)}
              disabled={providersLoading}
            >
              {providers.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>

            <span className="setting-label">Model</span>
            <select
              className="setting-select"
              value={defaultModel}
              onChange={(e) => setDefaultModel(e.target.value)}
              disabled={providersLoading}
            >
              {getModelsForProvider(defaultProvider).map(model => (
                <option key={model} value={model}>{model}</option>
              ))}
            </select>

            {providerSupportsReasoning(defaultProvider) && (
              <>
                <span className="setting-label">Reasoning</span>
                <select
                  className="setting-select"
                  value={defaultReasoning}
                  onChange={(e) => setDefaultReasoning(e.target.value)}
                >
                  {reasoningLevels.map(level => (
                    <option key={level} value={level}>
                      {level.charAt(0).toUpperCase() + level.slice(1)}
                    </option>
                  ))}
                </select>
              </>
            )}
          </div>
        </div>

        <div className="config-section">
          <h3>Select Opponents</h3>
          
          <div className="search-box">
            <input
              type="text"
              placeholder="Search personalities..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="search-input"
            />
            <Search className="search-icon" size={18} />
          </div>

          {loading ? (
            <div className="loading">Loading personalities...</div>
          ) : (
            <div className="personality-grid">
              {filteredPersonalities.map(([name, personality]) => (
                <button
                  key={name}
                  className={`personality-card ${
                    selectedPersonalities.includes(name) ? 'selected' : ''
                  } ${selectedPersonalities.length >= 5 && !selectedPersonalities.includes(name) ? 'disabled' : ''}`}
                  onClick={() => togglePersonality(name)}
                  disabled={selectedPersonalities.length >= 5 && !selectedPersonalities.includes(name)}
                >
                  <div className="personality-header">
                    <h4>{name}</h4>
                    <div className="personality-badges">
                      {opponentConfigs[name] && (
                        <span className="custom-config-badge" title="Custom LLM config">
                          <Settings size={14} />
                        </span>
                      )}
                      {selectedPersonalities.includes(name) && (
                        <Check className="checkmark" size={20} />
                      )}
                    </div>
                  </div>

                  <p className="play-style">{personality.play_style}</p>

                  <div className="traits">
                    <div className="cgc-personality-trait">
                      <span>Bluff</span>
                      {getTraitBar(personality.personality_traits.bluff_tendency)}
                    </div>
                    <div className="cgc-personality-trait">
                      <span>Aggro</span>
                      {getTraitBar(personality.personality_traits.aggression)}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

      <div className="custom-config__footer">
        <button
          className="configure-opponents-button"
          onClick={() => setShowConfigScreen(true)}
          disabled={selectedPersonalities.length === 0}
          title={selectedPersonalities.length === 0 ? 'Select opponents first' : undefined}
        >
          <Settings size={18} />
          Opponent AI
          {customConfigCount > 0 && (
            <span className="config-count">({customConfigCount} custom)</span>
          )}
        </button>
        <button
          className="start-button"
          onClick={handleStartGame}
          disabled={selectedPersonalities.length === 0}
        >
          Start Game with {selectedPersonalities.length} Opponent{selectedPersonalities.length !== 1 ? 's' : ''}
        </button>
      </div>
    </PageLayout>
  );
}