import { useState, useEffect } from 'react';
import { PageLayout, PageHeader } from '../shared';
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
    <PageLayout variant="centered" glowColor="gold" maxWidth="sm">
      <div className="name-entry-card">
        <PageHeader
          title="Welcome to My Poker Face"
          subtitle="Play against AI personalities with unique playing styles"
          titleVariant="primary"
        />

        <form onSubmit={handleSubmit} className="name-entry-form">
          <div className="name-entry-form__group">
            <label htmlFor="playerName" className="name-entry-form__label">
              What's your name?
            </label>
            <input
              type="text"
              id="playerName"
              value={playerName}
              onChange={handleChange}
              placeholder="Enter your name"
              maxLength={20}
              autoFocus
              className="name-entry-form__input"
            />
            {error && <div className="name-entry-form__error">{error}</div>}
          </div>

          <button type="submit" className="name-entry-form__submit">
            Join Table
          </button>
        </form>

        <div className="name-entry-preview">
          <p className="name-entry-preview__label">You'll be playing against:</p>
          <div className="name-entry-preview__items">
            <span className="name-entry-preview__item">Random Celebrity AIs</span>
            <span className="name-entry-preview__item">Unique Play Styles</span>
            <span className="name-entry-preview__item">Dynamic Personalities</span>
          </div>
        </div>
      </div>
    </PageLayout>
  );
}