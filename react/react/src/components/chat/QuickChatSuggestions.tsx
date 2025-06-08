import { useState, useEffect } from 'react';
import { config } from '../../config';
import './QuickChatSuggestions.css';

interface Suggestion {
  text: string;
  type: 'reaction' | 'strategic' | 'social';
}

interface QuickChatSuggestionsProps {
  gameId: string;
  playerName: string;
  isPlayerTurn: boolean;
  lastAction?: {
    type: string;
    player: string;
    amount?: number;
  };
  onSelectSuggestion: (text: string) => void;
}

export function QuickChatSuggestions({
  gameId,
  playerName,
  isPlayerTurn,
  lastAction,
  onSelectSuggestion
}: QuickChatSuggestionsProps) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastFetchTime, setLastFetchTime] = useState(0);

  const fetchSuggestions = async () => {
    // Prevent too frequent requests (30 second cooldown)
    const now = Date.now();
    if (now - lastFetchTime < 30000) return;

    setLoading(true);
    try {
      const response = await fetch(`${config.API_URL}/api/game/${gameId}/chat-suggestions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          playerName,
          lastAction,
          // Add more context as needed
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setSuggestions(data.suggestions || []);
        setLastFetchTime(now);
      }
    } catch (error) {
      console.error('Failed to fetch chat suggestions:', error);
    } finally {
      setLoading(false);
    }
  };

  // Fetch suggestions when it becomes player's turn
  useEffect(() => {
    if (isPlayerTurn && suggestions.length === 0) {
      fetchSuggestions();
    }
  }, [isPlayerTurn]);

  // Fetch new suggestions when significant action happens
  useEffect(() => {
    if (lastAction && isPlayerTurn) {
      // Only refresh if action is significant (raise, all-in, etc)
      if (['raise', 'all-in', 'all_in'].includes(lastAction.type)) {
        fetchSuggestions();
      }
    }
  }, [lastAction]);

  if (!isPlayerTurn || suggestions.length === 0) {
    return null;
  }

  const handleSuggestionClick = (text: string) => {
    onSelectSuggestion(text);
    // Optionally clear suggestions after use
    // setSuggestions([]);
  };

  const getTypeEmoji = (type: string) => {
    switch (type) {
      case 'reaction': return '😎';
      case 'strategic': return '🧠';
      case 'social': return '💬';
      default: return '💭';
    }
  };

  return (
    <div className="quick-chat-suggestions">
      <div className="suggestions-container">
        {loading ? (
          <div className="suggestion-pill loading">
            <span className="loading-dots">...</span>
          </div>
        ) : (
          suggestions.map((suggestion, index) => (
            <button
              key={index}
              className={`suggestion-pill suggestion-${suggestion.type}`}
              onClick={() => handleSuggestionClick(suggestion.text)}
              title={`${suggestion.type} message`}
            >
              <span className="suggestion-emoji">
                {getTypeEmoji(suggestion.type)}
              </span>
              <span className="suggestion-text">{suggestion.text}</span>
            </button>
          ))
        )}
        <button
          className="refresh-suggestions"
          onClick={fetchSuggestions}
          disabled={loading}
          title="Get new suggestions"
        >
          🔄
        </button>
      </div>
    </div>
  );
}