import { useState, useEffect } from 'react';
import './PlayerNameEntry.css';

interface PlayerNameEntryProps {
  onSubmit: (name: string) => void;
}

export function PlayerNameEntry({ onSubmit }: PlayerNameEntryProps) {
  const [playerName, setPlayerName] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    // Load saved name from localStorage
    const savedName = localStorage.getItem('pokerPlayerName');
    if (savedName) {
      setPlayerName(savedName);
    }
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    const trimmedName = playerName.trim();
    
    if (!trimmedName) {
      setError('Please enter your name');
      return;
    }
    
    if (trimmedName.length < 2) {
      setError('Name must be at least 2 characters');
      return;
    }
    
    if (trimmedName.length > 20) {
      setError('Name must be 20 characters or less');
      return;
    }
    
    // Save to localStorage
    localStorage.setItem('pokerPlayerName', trimmedName);
    
    // Call the onSubmit callback
    onSubmit(trimmedName);
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setPlayerName(e.target.value);
    setError(''); // Clear error when user types
  };

  return (
    <div className="player-name-entry">
      <div className="name-entry-card">
        <h1>Welcome to My Poker Face</h1>
        <p className="subtitle">Play against AI personalities with unique playing styles</p>
        
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="playerName">What's your name?</label>
            <input
              type="text"
              id="playerName"
              value={playerName}
              onChange={handleChange}
              placeholder="Enter your name"
              maxLength={20}
              autoFocus
            />
            {error && <div className="error-message">{error}</div>}
          </div>
          
          <button type="submit" className="submit-button">
            Join Table
          </button>
        </form>
        
        <div className="ai-preview">
          <p>You'll be playing against:</p>
          <div className="ai-personalities">
            <span className="personality">🎭 Random Celebrity AIs</span>
            <span className="personality">🤖 Unique Play Styles</span>
            <span className="personality">💬 Dynamic Personalities</span>
          </div>
        </div>
      </div>
    </div>
  );
}