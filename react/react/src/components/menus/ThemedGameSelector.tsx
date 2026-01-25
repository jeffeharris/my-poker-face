import { useState, useEffect } from 'react';
import {
  FlaskConical, Clapperboard, Medal, Crown, Music, Laugh, Skull, Sparkles, Dices,
  type LucideIcon
} from 'lucide-react';
import { config } from '../../config';
import { PageLayout, PageHeader, MenuBar } from '../shared';
import './ThemedGameSelector.css';

interface Theme {
  id: string;
  name: string;
  description: string;
  icon: LucideIcon;
  personalities?: string[];
}

interface ThemedGameSelectorProps {
  onSelectTheme: (theme: Theme) => Promise<void>;
  onBack: () => void;
}

// Predefined theme prompts that will be sent to OpenAI
const THEME_PROMPTS: Theme[] = [
  { id: 'science', name: 'Science Masters', icon: FlaskConical, description: 'Great minds think alike... or do they?' },
  { id: 'hollywood', name: 'Hollywood Legends', icon: Clapperboard, description: 'Lights, camera, all-in!' },
  { id: 'sports', name: 'Sports Champions', icon: Medal, description: 'Bring your A-game to the table' },
  { id: 'history', name: 'Historical Figures', icon: Crown, description: 'Making history one hand at a time' },
  { id: 'music', name: 'Music Icons', icon: Music, description: 'Feel the rhythm of the cards' },
  { id: 'comedy', name: 'Comedy Legends', icon: Laugh, description: 'No joke - these players are serious!' },
  { id: 'villains', name: 'Famous Villains', icon: Skull, description: 'Sometimes it pays to be bad' },
  { id: 'surprise', name: 'Surprise Me!', icon: Sparkles, description: 'A mysterious mix of personalities' }
];

export function ThemedGameSelector({ onSelectTheme, onBack }: ThemedGameSelectorProps) {
  const [themes, setThemes] = useState<Theme[]>([]);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setThemes(THEME_PROMPTS);
  }, []);

  const handleGenerateTheme = async (theme: Theme) => {
    setGenerating(true);
    setError(null);

    try {
      let response: Response;
      try {
        response = await fetch(`${config.API_URL}/api/generate-theme`, {
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
      } catch {
        throw new Error('Network error. Please check your connection and try again.');
      }

      if (response.status === 429) {
        throw new Error('Rate limit exceeded. Please wait a few minutes before trying again.');
      }

      if (!response.ok) {
        throw new Error('Failed to generate theme. Please try again.');
      }

      const data = await response.json();

      // Add the generated personalities to the theme
      const themedGame = {
        ...theme,
        personalities: data.personalities
      };

      await onSelectTheme(themedGame);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to generate themed game. Please try again.';
      setError(errorMessage);
      console.error('Theme generation error:', err);
    } finally {
      setGenerating(false);
    }
  };

  return (
    <>
      <MenuBar onBack={onBack} title="Themed Game" showUserInfo />
      <PageLayout variant="top" glowColor="amber" maxWidth="lg" hasMenuBar>
        <PageHeader
          title="Choose Your Theme"
          subtitle="Each theme brings together unique personalities for an unforgettable game!"
          titleVariant="primary"
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
              <div className="theme-icon"><theme.icon size={32} /></div>
              <h3>{theme.name}</h3>
              <p>{theme.description}</p>
              {theme.id === 'surprise' && (
                <div className="surprise-badge"><Dices size={16} /></div>
              )}
            </button>
          ))}
        </div>

        {generating && (
          <div className="generating-overlay">
            <div className="generating-content">
              <div className="generating-cards">
                {['♠', '♥', '♦', '♣'].map((suit, i) => (
                  <div key={i} className={`generating-card suit-${i}`}>{suit}</div>
                ))}
              </div>
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
    </>
  );
}