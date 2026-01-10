import { useState, useEffect } from 'react';
import { Search, Check } from 'lucide-react';
import { config } from '../../config';
import { PageLayout, PageHeader } from '../shared';
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

interface LLMConfig {
  model: string;
  reasoning_effort: string;
  starting_stack?: number;
  big_blind?: number;
  blind_growth?: number;
  blinds_increase?: number;
  max_blind?: number;
}

interface CustomGameConfigProps {
  onStartGame: (selectedPersonalities: string[], llmConfig: LLMConfig) => void;
  onBack: () => void;
}

export function CustomGameConfig({ onStartGame, onBack }: CustomGameConfigProps) {
  const [personalities, setPersonalities] = useState<{ [key: string]: Personality }>({});
  const [selectedPersonalities, setSelectedPersonalities] = useState<string[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [loading, setLoading] = useState(true);

  // Model configuration state
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState('gpt-5-nano');
  const [reasoningLevels] = useState(['minimal', 'low']);
  const [selectedReasoning, setSelectedReasoning] = useState('minimal');
  const [modelsLoading, setModelsLoading] = useState(true);

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
    fetchModels();
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

  const fetchModels = async () => {
    try {
      const response = await fetch(`${config.API_URL}/api/models`, { credentials: 'include' });
      const data = await response.json();
      if (data.success) {
        setAvailableModels(data.models);
        setSelectedModel(data.default_model || 'gpt-5-nano');
        // Only use API default if it's in our allowed list (minimal, low)
        const apiReasoning = data.default_reasoning;
        if (apiReasoning === 'minimal' || apiReasoning === 'low') {
          setSelectedReasoning(apiReasoning);
        }
      }
    } catch (error) {
      console.error('Failed to fetch models:', error);
      // Use defaults on error
      setAvailableModels(['gpt-5-nano', 'gpt-5-mini', 'gpt-5']);
    } finally {
      setModelsLoading(false);
    }
  };

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
      const llmConfig: LLMConfig = {
        model: selectedModel,
        reasoning_effort: selectedReasoning,
        starting_stack: startingStack,
        big_blind: bigBlind,
        blind_growth: blindGrowth,
        blinds_increase: blindsIncrease,
        max_blind: maxBlind
      };
      onStartGame(selectedPersonalities, llmConfig);
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
          <div className="settings-grid">
            <div className="setting">
              <label>Number of Opponents</label>
              <div className="selected-count">
                {selectedPersonalities.length} / 5
              </div>
            </div>

            <div className="setting-row">
              <div className="setting">
                <label>AI Model</label>
                <select
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  disabled={modelsLoading}
                >
                  {availableModels.map(model => (
                    <option key={model} value={model}>{model}</option>
                  ))}
                </select>
              </div>

              <div className="setting">
                <label>Reasoning</label>
                <select
                  value={selectedReasoning}
                  onChange={(e) => setSelectedReasoning(e.target.value)}
                >
                  {reasoningLevels.map(level => (
                    <option key={level} value={level}>
                      {level.charAt(0).toUpperCase() + level.slice(1)}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="setting-row">
              <div className="setting">
                <label>Starting Stack</label>
                <select
                  value={startingStack}
                  onChange={(e) => setStartingStack(parseInt(e.target.value))}
                >
                  {stackOptions.map(stack => (
                    <option key={stack} value={stack}>{stack.toLocaleString()}</option>
                  ))}
                </select>
              </div>

              <div className="setting">
                <label>Big Blind</label>
                <select
                  value={bigBlind}
                  onChange={(e) => setBigBlind(parseInt(e.target.value))}
                >
                  {blindOptions.map(blind => (
                    <option key={blind} value={blind}>{blind}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="setting-row">
              <div className="setting">
                <label>Blinds Increase</label>
                <select
                  value={blindsIncrease}
                  onChange={(e) => setBlindsIncrease(parseInt(e.target.value))}
                >
                  {blindsIncreaseOptions.map(hands => (
                    <option key={hands} value={hands}>Every {hands} hands</option>
                  ))}
                </select>
              </div>

              <div className="setting">
                <label>Blind Growth</label>
                <select
                  value={blindGrowth}
                  onChange={(e) => setBlindGrowth(parseFloat(e.target.value))}
                >
                  {blindGrowthOptions.map(rate => (
                    <option key={rate} value={rate}>{rate}x</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="setting-row">
              <div className="setting">
                <label>Blind Cap</label>
                <select
                  value={maxBlind}
                  onChange={(e) => setMaxBlind(parseInt(e.target.value))}
                >
                  {maxBlindOptions.map(cap => (
                    <option key={cap} value={cap}>{cap === 0 ? 'No cap' : cap.toLocaleString()}</option>
                  ))}
                </select>
              </div>
            </div>
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
                    {selectedPersonalities.includes(name) && (
                      <Check className="checkmark" size={20} />
                    )}
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