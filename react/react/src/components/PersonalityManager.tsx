import { useState, useEffect } from 'react';
import { config } from '../config';
import './PersonalityManager.css';

interface PersonalityTrait {
  bluff_tendency: number;
  aggression: number;
  chattiness: number;
  emoji_usage: number;
}

interface Personality {
  play_style: string;
  default_confidence: string;
  default_attitude: string;
  personality_traits: PersonalityTrait;
  verbal_tics: string[];
  physical_tics: string[];
}

interface PersonalityManagerProps {
  onBack: () => void;
}

export function PersonalityManager({ onBack }: PersonalityManagerProps) {
  const [personalities, setPersonalities] = useState<Record<string, Personality>>({});
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [editingPersonality, setEditingPersonality] = useState<Personality | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error' | 'info', text: string } | null>(null);

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
      showMessage('error', 'Failed to load personalities');
    }
  };

  const showMessage = (type: 'success' | 'error' | 'info', text: string) => {
    setMessage({ type, text });
    setTimeout(() => setMessage(null), 5000);
  };

  const selectPersonality = (name: string) => {
    setSelectedName(name);
    setEditingPersonality({ ...personalities[name] });
  };

  const updateTrait = (trait: keyof PersonalityTrait, value: number) => {
    if (!editingPersonality) return;
    setEditingPersonality({
      ...editingPersonality,
      personality_traits: {
        ...editingPersonality.personality_traits,
        [trait]: value
      }
    });
  };

  const updateArrayField = (field: 'verbal_tics' | 'physical_tics', index: number, value: string) => {
    if (!editingPersonality) return;
    const newArray = [...editingPersonality[field]];
    newArray[index] = value;
    setEditingPersonality({
      ...editingPersonality,
      [field]: newArray
    });
  };

  const addArrayItem = (field: 'verbal_tics' | 'physical_tics') => {
    if (!editingPersonality) return;
    setEditingPersonality({
      ...editingPersonality,
      [field]: [...editingPersonality[field], '']
    });
  };

  const removeArrayItem = (field: 'verbal_tics' | 'physical_tics', index: number) => {
    if (!editingPersonality) return;
    const newArray = editingPersonality[field].filter((_, i) => i !== index);
    setEditingPersonality({
      ...editingPersonality,
      [field]: newArray
    });
  };

  const savePersonality = async () => {
    if (!selectedName || !editingPersonality) return;

    try {
      const response = await fetch(`${config.API_URL}/api/personality/${selectedName}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(editingPersonality)
      });
      
      const data = await response.json();
      if (data.success) {
        showMessage('success', 'Personality saved successfully');
        setPersonalities({
          ...personalities,
          [selectedName]: editingPersonality
        });
      } else {
        showMessage('error', data.error || 'Failed to save');
      }
    } catch (error) {
      showMessage('error', 'Network error');
    }
  };

  const deletePersonality = async () => {
    if (!selectedName || !confirm(`Delete "${selectedName}"?`)) return;

    try {
      const response = await fetch(`${config.API_URL}/api/personality/${selectedName}`, {
        method: 'DELETE'
      });
      
      const data = await response.json();
      if (data.success) {
        showMessage('success', 'Personality deleted');
        const newPersonalities = { ...personalities };
        delete newPersonalities[selectedName];
        setPersonalities(newPersonalities);
        setSelectedName(null);
        setEditingPersonality(null);
      }
    } catch (error) {
      showMessage('error', 'Failed to delete');
    }
  };

  const createNewPersonality = async () => {
    const name = prompt('Enter name for new personality:');
    if (!name || personalities[name]) {
      if (personalities[name]) showMessage('error', 'Personality already exists');
      return;
    }

    if (confirm(`Generate personality for "${name}" with AI?`)) {
      generateWithAI(name);
    } else {
      // Create manual personality
      const newPersonality: Personality = {
        play_style: "balanced",
        default_confidence: "confident",
        default_attitude: "focused",
        personality_traits: {
          bluff_tendency: 0.5,
          aggression: 0.5,
          chattiness: 0.5,
          emoji_usage: 0.3
        },
        verbal_tics: [],
        physical_tics: []
      };
      
      setPersonalities({ ...personalities, [name]: newPersonality });
      selectPersonality(name);
    }
  };

  const generateWithAI = async (name: string, force = false) => {
    setLoading(true);
    showMessage('info', `Generating personality for ${name}...`);

    try {
      const response = await fetch(`${config.API_URL}/api/generate_personality`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, force })
      });
      
      const data = await response.json();
      if (data.success) {
        setPersonalities({ ...personalities, [name]: data.personality });
        selectPersonality(name);
        showMessage('success', `AI generated personality for ${name}!`);
      } else {
        showMessage('error', data.message || 'Generation failed');
      }
    } catch (error) {
      showMessage('error', 'Network error during generation');
    } finally {
      setLoading(false);
    }
  };

  const regeneratePersonality = () => {
    if (!selectedName) return;
    if (confirm(`Regenerate personality for "${selectedName}"?`)) {
      generateWithAI(selectedName, true);
    }
  };

  return (
    <div className="personality-manager">
      <div className="pm-header">
        <button className="pm-back-btn" onClick={onBack}>← Back</button>
        <h1>AI Personality Manager</h1>
      </div>

      {message && (
        <div className={`pm-alert pm-alert-${message.type}`}>
          {message.text}
        </div>
      )}

      <div className="pm-grid">
        <div className="pm-sidebar">
          <button className="pm-new-btn" onClick={createNewPersonality}>
            + Create New Personality
          </button>
          
          <h3>Available Personalities</h3>
          <div className="pm-list">
            {Object.keys(personalities).map(name => (
              <div
                key={name}
                className={`pm-list-item ${selectedName === name ? 'active' : ''}`}
                onClick={() => selectPersonality(name)}
              >
                {name}
              </div>
            ))}
          </div>
        </div>

        <div className="pm-editor">
          {editingPersonality && selectedName ? (
            <>
              <h2>Editing: {selectedName}</h2>
              
              <div className="pm-field">
                <label>Play Style</label>
                <input
                  type="text"
                  value={editingPersonality.play_style}
                  onChange={(e) => setEditingPersonality({
                    ...editingPersonality,
                    play_style: e.target.value
                  })}
                  placeholder="e.g., aggressive and unpredictable"
                />
              </div>

              <div className="pm-field">
                <label>Default Confidence</label>
                <input
                  type="text"
                  value={editingPersonality.default_confidence}
                  onChange={(e) => setEditingPersonality({
                    ...editingPersonality,
                    default_confidence: e.target.value
                  })}
                  placeholder="e.g., overconfident, steady"
                />
              </div>

              <div className="pm-field">
                <label>Default Attitude</label>
                <input
                  type="text"
                  value={editingPersonality.default_attitude}
                  onChange={(e) => setEditingPersonality({
                    ...editingPersonality,
                    default_attitude: e.target.value
                  })}
                  placeholder="e.g., friendly, hostile"
                />
              </div>

              <h3>Personality Traits</h3>
              
              <div className="pm-trait">
                <label>
                  Bluff Tendency: {Math.round(editingPersonality.personality_traits.bluff_tendency * 100)}%
                </label>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={editingPersonality.personality_traits.bluff_tendency * 100}
                  onChange={(e) => updateTrait('bluff_tendency', Number(e.target.value) / 100)}
                />
              </div>

              <div className="pm-trait">
                <label>
                  Aggression: {Math.round(editingPersonality.personality_traits.aggression * 100)}%
                </label>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={editingPersonality.personality_traits.aggression * 100}
                  onChange={(e) => updateTrait('aggression', Number(e.target.value) / 100)}
                />
              </div>

              <div className="pm-trait">
                <label>
                  Chattiness: {Math.round(editingPersonality.personality_traits.chattiness * 100)}%
                </label>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={editingPersonality.personality_traits.chattiness * 100}
                  onChange={(e) => updateTrait('chattiness', Number(e.target.value) / 100)}
                />
              </div>

              <div className="pm-trait">
                <label>
                  Emoji Usage: {Math.round(editingPersonality.personality_traits.emoji_usage * 100)}%
                </label>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={editingPersonality.personality_traits.emoji_usage * 100}
                  onChange={(e) => updateTrait('emoji_usage', Number(e.target.value) / 100)}
                />
              </div>

              <h3>Verbal Tics</h3>
              <div className="pm-array-items">
                {editingPersonality.verbal_tics.map((tic, index) => (
                  <div key={index} className="pm-array-item">
                    <input
                      type="text"
                      value={tic}
                      onChange={(e) => updateArrayField('verbal_tics', index, e.target.value)}
                      placeholder="Enter verbal tic"
                    />
                    <button onClick={() => removeArrayItem('verbal_tics', index)}>×</button>
                  </div>
                ))}
                <button className="pm-add-btn" onClick={() => addArrayItem('verbal_tics')}>
                  + Add Verbal Tic
                </button>
              </div>

              <h3>Physical Tics</h3>
              <div className="pm-array-items">
                {editingPersonality.physical_tics.map((tic, index) => (
                  <div key={index} className="pm-array-item">
                    <input
                      type="text"
                      value={tic}
                      onChange={(e) => updateArrayField('physical_tics', index, e.target.value)}
                      placeholder="Enter physical tic (use *asterisks*)"
                    />
                    <button onClick={() => removeArrayItem('physical_tics', index)}>×</button>
                  </div>
                ))}
                <button className="pm-add-btn" onClick={() => addArrayItem('physical_tics')}>
                  + Add Physical Tic
                </button>
              </div>

              <div className="pm-buttons">
                <button className="pm-btn pm-btn-primary" onClick={savePersonality}>
                  Save Changes
                </button>
                <button className="pm-btn pm-btn-warning" onClick={regeneratePersonality}>
                  Regenerate with AI
                </button>
                <button className="pm-btn pm-btn-danger" onClick={deletePersonality}>
                  Delete
                </button>
              </div>
            </>
          ) : (
            <div className="pm-no-selection">
              Select a personality to edit or create a new one
            </div>
          )}
        </div>
      </div>

      {loading && (
        <div className="pm-loading">
          <div className="pm-spinner"></div>
          <p>Generating personality...</p>
        </div>
      )}
    </div>
  );
}