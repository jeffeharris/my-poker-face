import { useState, useEffect } from 'react';
import { config } from '../../config';
import { PageLayout, PageHeader } from '../shared';
import './ThemedGameSelector.css';

interface Theme {
  id: string;
  name: string;
  description: string;
  icon: string;
  personalities?: string[];
}

interface ThemedGameSelectorProps {
  onSelectTheme: (theme: Theme) => void;
  onBack: () => void;
}

export function ThemedGameSelector({ onSelectTheme, onBack }: ThemedGameSelectorProps) {
  const [themes, setThemes] = useState<Theme[]>([]);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Predefined theme prompts that will be sent to OpenAI
  const themePrompts = [
    { id: 'science', name: 'Science Masters', icon: '🧪', description: 'Great minds think alike... or do they?' },
    { id: 'hollywood', name: 'Hollywood Legends', icon: '🎬', description: 'Lights, camera, all-in!' },
    { id: 'sports', name: 'Sports Champions', icon: '🏅', description: 'Bring your A-game to the table' },
    { id: 'history', name: 'Historical Figures', icon: '👑', description: 'Making history one hand at a time' },
    { id: 'music', name: 'Music Icons', icon: '🎵', description: 'Feel the rhythm of the cards' },
    { id: 'comedy', name: 'Comedy Legends', icon: '😂', description: 'No joke - these players are serious!' },
    { id: 'villains', name: 'Famous Villains', icon: '😈', description: 'Sometimes it pays to be bad' },
    { id: 'surprise', name: 'Surprise Me!', icon: '✨', description: 'A mysterious mix of personalities' }
  ];

  useEffect(() => {
    setThemes(themePrompts);
  }, []);

  const handleGenerateTheme = async (theme: Theme) => {
    setGenerating(true);
    setError(null);

    try {
      const response = await fetch(`${config.API_URL}/api/generate-theme`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          theme: theme.id,
          themeName: theme.name,
          description: theme.description
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to generate theme');
      }

      const data = await response.json();
      
      // Add the generated personalities to the theme
      const themedGame = {
        ...theme,
        personalities: data.personalities
      };

      onSelectTheme(themedGame);
    } catch (err) {
      setError('Failed to generate themed game. Please try again.');
      console.error('Theme generation error:', err);
    } finally {
      setGenerating(false);
    }
  };

  return (
    <PageLayout variant="top" glowColor="amber" maxWidth="lg">
      <PageHeader
        title="Choose Your Theme"
        subtitle="Each theme brings together unique personalities for an unforgettable game!"
        onBack={onBack}
        titleVariant="themed"
      />

        {error && (
          <div className="error-message">
            {error}
          </div>
        )}

        <div className="theme-grid">
          {themes.map((theme) => (
            <button
              key={theme.id}
              className={`theme-card ${theme.id}`}
              onClick={() => handleGenerateTheme(theme)}
              disabled={generating}
            >
              <div className="theme-icon">{theme.icon}</div>
              <h3>{theme.name}</h3>
              <p>{theme.description}</p>
              {theme.id === 'surprise' && (
                <div className="surprise-badge">🎲</div>
              )}
            </button>
          ))}
        </div>

        {generating && (
          <div className="generating-overlay">
            <div className="generating-content">
              <div className="generating-spinner">🎰</div>
              <h3>Assembling your table...</h3>
              <p>Finding the perfect personalities for your theme</p>
            </div>
          </div>
        )}

      <div className="themed-selector__footer">
        <p className="hint">
          Personalities won't be revealed until the game starts - it's part of the surprise!
        </p>
      </div>
    </PageLayout>
  );
}