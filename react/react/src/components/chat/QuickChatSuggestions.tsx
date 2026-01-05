import { useState, useCallback, useEffect } from 'react';
import type { Player } from '../../types';
import type { ChatTone, ChatLength, ChatIntensity, TargetedSuggestion } from '../../types/chat';
import { gameAPI } from '../../utils/api';
import { config } from '../../config';
import './QuickChatSuggestions.css';

// Cooldown between suggestion fetches to prevent API spam
const SUGGESTION_FETCH_COOLDOWN_MS = 15000;

interface QuickChatSuggestionsProps {
  gameId: string;
  playerName: string;
  players: Player[];
  lastAction?: {
    type: string;
    player: string;
    amount?: number;
  };
  onSelectSuggestion: (text: string) => void;
  defaultExpanded?: boolean;
  hideHeader?: boolean;
}

interface ToneOption {
  id: ChatTone;
  emoji: string;
  label: string;
}

const TONE_OPTIONS: ToneOption[] = [
  { id: 'tilt', emoji: '🔥', label: 'Tilt' },
  { id: 'false_confidence', emoji: '😇', label: 'False Confidence' },
  { id: 'doubt', emoji: '🤨', label: 'Doubt' },
  { id: 'goad', emoji: '😈', label: 'Goad' },
  { id: 'mislead', emoji: '🎭', label: 'Mislead' },
  { id: 'befriend', emoji: '🤝', label: 'Befriend' },
];

export function QuickChatSuggestions({
  gameId,
  playerName,
  players,
  lastAction,
  onSelectSuggestion,
  defaultExpanded = false,
  hideHeader = false
}: QuickChatSuggestionsProps) {
  const [selectedTarget, setSelectedTarget] = useState<string | null>(null);
  const [selectedTone, setSelectedTone] = useState<ChatTone | null>(null);
  const [suggestions, setSuggestions] = useState<TargetedSuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastFetchTime, setLastFetchTime] = useState(0);
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);

  // Length and intensity toggles with localStorage persistence
  const [length, setLength] = useState<ChatLength>(
    () => (localStorage.getItem('quickchat_length') as ChatLength) || 'short'
  );
  const [intensity, setIntensity] = useState<ChatIntensity>(
    () => (localStorage.getItem('quickchat_intensity') as ChatIntensity) || 'chill'
  );

  // Persist toggles to localStorage
  useEffect(() => {
    localStorage.setItem('quickchat_length', length);
  }, [length]);

  useEffect(() => {
    localStorage.setItem('quickchat_intensity', intensity);
  }, [intensity]);

  // Get AI players (non-human, not folded)
  const aiPlayers = players.filter(p => !p.is_human && !p.is_folded);

  const fetchSuggestions = useCallback(async (target: string | null, tone: ChatTone) => {
    // Cooldown check
    const now = Date.now();
    if (now - lastFetchTime < SUGGESTION_FETCH_COOLDOWN_MS) {
      return;
    }

    setLoading(true);
    try {
      // Convert 'table' to null for API (general table talk)
      const apiTarget = target === 'table' ? null : target;
      const response = await gameAPI.getTargetedChatSuggestions(
        gameId,
        playerName,
        apiTarget,
        tone,
        length,
        intensity,
        lastAction
      );
      if (response.fallback) {
        console.warn('[QuickChat] Using fallback suggestions! API error:', response.error);
      }
      setSuggestions(response.suggestions || []);
      setLastFetchTime(now);
    } catch (error) {
      console.error('[QuickChat] Failed to fetch suggestions:', error);
      // Set fallback suggestions
      setSuggestions([
        { text: 'Nice play!', tone },
        { text: 'Interesting...', tone }
      ]);
    } finally {
      setLoading(false);
    }
  }, [gameId, playerName, lastAction, lastFetchTime, length, intensity]);

  const handleTargetSelect = (target: string | null) => {
    setSelectedTarget(target);
    setSuggestions([]); // Clear old suggestions
    // If tone is already selected, fetch new suggestions
    if (selectedTone) {
      fetchSuggestions(target, selectedTone);
    }
  };

  const handleToneSelect = (tone: ChatTone) => {
    setSelectedTone(tone);
    // Only fetch if a target is selected
    if (selectedTarget) {
      fetchSuggestions(selectedTarget, tone);
    }
  };

  const handleSuggestionClick = (text: string) => {
    onSelectSuggestion(text);
    // Reset state after selection
    setSelectedTarget(null);
    setSelectedTone(null);
    setSuggestions([]);
    setIsExpanded(false);
  };

  const handleRefresh = () => {
    if (selectedTone) {
      setLastFetchTime(0); // Reset cooldown
      fetchSuggestions(selectedTarget, selectedTone);
    }
  };

  // Collapsed state - just show the toggle button
  if (!isExpanded) {
    return (
      <div className="quick-chat-collapsed">
        <button
          className="quick-chat-toggle"
          onClick={() => setIsExpanded(true)}
          title="Quick chat suggestions"
        >
          <span className="toggle-emoji">💬</span>
          <span className="toggle-text">Quick Chat</span>
        </button>
      </div>
    );
  }

  return (
    <div className="quick-chat-suggestions">
      {/* Header with collapse button - hidden when used in overlay */}
      {!hideHeader && (
        <div className="quick-chat-header">
          <span className="header-title">Quick Chat</span>
          <button
            className="collapse-btn"
            onClick={() => {
              setIsExpanded(false);
              setSelectedTarget(null);
              setSelectedTone(null);
              setSuggestions([]);
            }}
            title="Close"
          >
            ×
          </button>
        </div>
      )}

      {/* Target selector */}
      <div className="target-selector">
        <div className="selector-label">Who?</div>
        <div className="target-options">
          <button
            className={`target-btn ${selectedTarget === 'table' ? 'selected' : ''}`}
            onClick={() => handleTargetSelect('table')}
            title="Talk to the table"
          >
            <span className="target-avatar">🎲</span>
            <span className="target-name">Table</span>
          </button>
          {aiPlayers.map((player) => (
            <button
              key={player.name}
              className={`target-btn ${selectedTarget === player.name ? 'selected' : ''}`}
              onClick={() => handleTargetSelect(player.name)}
              title={`Talk to ${player.name}`}
            >
              <span className={`target-avatar ${player.avatar_url ? 'has-image' : ''}`}>
                {player.avatar_url ? (
                  <img
                    src={`${config.API_URL}${player.avatar_url}`}
                    alt={player.name}
                    className="target-avatar-img"
                  />
                ) : (
                  player.name.charAt(0).toUpperCase()
                )}
              </span>
              <span className="target-name">
                {player.name.split(' ')[0]}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Modifiers row */}
      <div className="modifier-toggles">
        <div className="toggle-group">
          <button
            className={`toggle-btn ${length === 'short' ? 'active' : ''}`}
            onClick={() => setLength('short')}
            title="Short responses"
          >
            Short
          </button>
          <button
            className={`toggle-btn ${length === 'long' ? 'active' : ''}`}
            onClick={() => setLength('long')}
            title="Longer responses"
          >
            Long
          </button>
        </div>
        <div className="toggle-group">
          <button
            className={`toggle-btn ${intensity === 'chill' ? 'active' : ''}`}
            onClick={() => setIntensity('chill')}
            title="Playful and light"
          >
            Chill
          </button>
          <button
            className={`toggle-btn ${intensity === 'spicy' ? 'active' : ''}`}
            onClick={() => setIntensity('spicy')}
            title="No filter"
          >
            🌶️
          </button>
        </div>
      </div>

      {/* Tone selector */}
      <div className="tone-selector">
        <div className="selector-label">Goal?</div>
        <div className="tone-options">
          {TONE_OPTIONS.map((tone) => (
            <button
              key={tone.id}
              className={`tone-btn tone-${tone.id} ${selectedTone === tone.id ? 'selected' : ''}`}
              onClick={() => handleToneSelect(tone.id)}
              title={tone.label}
            >
              <span className="tone-emoji">{tone.emoji}</span>
              <span className="tone-label">{tone.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Suggestions display */}
      {(loading || suggestions.length > 0) && (
        <div className="suggestions-section">
          <div className="selector-label">Say:</div>
          <div className="suggestions-container">
            {loading ? (
              <div className="suggestion-loading">
                <span className="loading-dots">Thinking</span>
              </div>
            ) : (
              <>
                {suggestions.map((suggestion, index) => (
                  <button
                    key={index}
                    className={`suggestion-pill tone-${suggestion.tone}`}
                    onClick={() => handleSuggestionClick(suggestion.text)}
                  >
                    {suggestion.text}
                  </button>
                ))}
                <button
                  className="refresh-btn"
                  onClick={handleRefresh}
                  disabled={loading}
                  title="Get new suggestions"
                >
                  🔄
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
