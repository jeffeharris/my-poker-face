import { useState, useEffect } from 'react';
import { config } from '../../config';
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

interface CustomGameConfigProps {
  onStartGame: (selectedPersonalities: string[]) => void;
  onBack: () => void;
}

export function CustomGameConfig({ onStartGame, onBack }: CustomGameConfigProps) {
  const [personalities, setPersonalities] = useState<{ [key: string]: Personality }>({});
  const [selectedPersonalities, setSelectedPersonalities] = useState<string[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [loading, setLoading] = useState(true);
  const [difficulty, setDifficulty] = useState('normal');

  useEffect(() => {
    fetchPersonalities();
  }, []);

  const fetchPersonalities = async () => {
    try {
      const response = await fetch(`${config.API_URL}/api/personalities`);
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
      onStartGame(selectedPersonalities);
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
    <div className="custom-config">
      <div className="custom-config__container">
        <div className="custom-config__header">
          <button className="back-button" onClick={onBack}>
            ← Back
          </button>
          <h2>Custom Game Setup</h2>
          <p>Choose your opponents (up to 5)</p>
        </div>

        <div className="config-section">
          <h3>Game Settings</h3>
          <div className="settings-grid">
            <div className="setting">
              <label>Number of Opponents</label>
              <div className="selected-count">
                {selectedPersonalities.length} / 5
              </div>
            </div>
            
            <div className="setting">
              <label>Difficulty</label>
              <select 
                value={difficulty} 
                onChange={(e) => setDifficulty(e.target.value)}
                disabled
              >
                <option value="easy">Easy</option>
                <option value="normal">Normal</option>
                <option value="hard">Hard</option>
              </select>
              <span className="coming-soon">Coming soon</span>
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
            <span className="search-icon">🔍</span>
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
                      <span className="checkmark">✓</span>
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
      </div>
    </div>
  );
}