import { useState, useCallback, useEffect, useRef } from 'react';
import { MessageCircle, Flame, Smile, HelpCircle, Zap, Theater, Handshake, Users, type LucideIcon } from 'lucide-react';
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
  onSuggestionsLoaded?: () => void;
}

interface ToneOption {
  id: ChatTone;
  icon: LucideIcon;
  label: string;
}

const TONE_OPTIONS: ToneOption[] = [
  { id: 'tilt', icon: Flame, label: 'Tilt' },
  { id: 'false_confidence', icon: Smile, label: 'False Confidence' },
  { id: 'doubt', icon: HelpCircle, label: 'Doubt' },
  { id: 'goad', icon: Zap, label: 'Goad' },
  { id: 'mislead', icon: Theater, label: 'Mislead' },
  { id: 'befriend', icon: Handshake, label: 'Befriend' },
];

export function QuickChatSuggestions({
  gameId,
  playerName,
  players,
  lastAction,
  onSelectSuggestion,
  defaultExpanded = false,
  hideHeader = false,
  onSuggestionsLoaded
}: QuickChatSuggestionsProps) {
  const [selectedTarget, setSelectedTarget] = useState<string | null>(null);
  const [selectedTone, setSelectedTone] = useState<ChatTone | null>(null);
  const [suggestions, setSuggestions] = useState<TargetedSuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastFetchTime, setLastFetchTime] = useState(0);
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  const [containerHeight, setContainerHeight] = useState<number | null>(null);
  const suggestionsRef = useRef<HTMLDivElement>(null);

  // Cache suggestions by target+tone+length+intensity combination
  const suggestionsCache = useRef<Record<string, TargetedSuggestion[]>>({});

  // Length and intensity toggles with localStorage persistence
  const [length, setLength] = useState<ChatLength>(
    () => (localStorage.getItem('quickchat_length') as ChatLength) || 'short'
  );
  const [intensity, setIntensity] = useState<ChatIntensity>(
    () => (localStorage.getItem('quickchat_intensity') as ChatIntensity) || 'chill'
  );

  // Helper to generate cache key
  const getCacheKey = useCallback(
    (target: string | null, tone: ChatTone, len: ChatLength, int: ChatIntensity) =>
      `${target || 'table'}_${tone}_${len}_${int}`,
    []
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


  const fetchSuggestions = useCallback(async (target: string | null, tone: ChatTone, forceRefresh = false) => {
    // Cooldown check (skip if force refresh)
    const now = Date.now();
    if (!forceRefresh && now - lastFetchTime < SUGGESTION_FETCH_COOLDOWN_MS) {
      return;
    }

    // Capture current height before loading to prevent jitter
    if (suggestionsRef.current) {
      setContainerHeight(suggestionsRef.current.offsetHeight);
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
      const newSuggestions = response.suggestions || [];
      setSuggestions(newSuggestions);
      // Cache the suggestions
      const cacheKey = getCacheKey(target, tone, length, intensity);
      suggestionsCache.current[cacheKey] = newSuggestions;
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
      setContainerHeight(null); // Release fixed height
      onSuggestionsLoaded?.();
    }
  }, [gameId, playerName, lastAction, lastFetchTime, length, intensity, getCacheKey, onSuggestionsLoaded]);

  // Check cache when length/intensity changes and auto-fetch if no cache
  useEffect(() => {
    if (selectedTarget !== null && selectedTone !== null) {
      const cacheKey = getCacheKey(selectedTarget, selectedTone, length, intensity);
      const cached = suggestionsCache.current[cacheKey];
      if (cached) {
        setSuggestions(cached);
        onSuggestionsLoaded?.();
      } else {
        // No cache - capture height and fetch new suggestions
        if (suggestionsRef.current) {
          setContainerHeight(suggestionsRef.current.offsetHeight);
        }
        fetchSuggestions(selectedTarget, selectedTone, true);
      }
    }
  }, [length, intensity, selectedTarget, selectedTone, getCacheKey, fetchSuggestions]);

  const handleTargetSelect = (target: string | null) => {
    setSelectedTarget(target);
    // Check cache for this target with current tone
    if (selectedTone) {
      const cacheKey = getCacheKey(target, selectedTone, length, intensity);
      const cached = suggestionsCache.current[cacheKey];
      if (cached) {
        setSuggestions(cached);
        onSuggestionsLoaded?.();
      } else {
        // Capture height before fetching
        if (suggestionsRef.current) {
          setContainerHeight(suggestionsRef.current.offsetHeight);
        }
        fetchSuggestions(target, selectedTone, true);
      }
    } else {
      setSuggestions([]);
    }
  };

  const handleToneSelect = (tone: ChatTone) => {
    setSelectedTone(tone);
    // Check cache for this tone with current target
    if (selectedTarget) {
      const cacheKey = getCacheKey(selectedTarget, tone, length, intensity);
      const cached = suggestionsCache.current[cacheKey];
      if (cached) {
        setSuggestions(cached);
        onSuggestionsLoaded?.();
      } else {
        // Capture height before fetching
        if (suggestionsRef.current) {
          setContainerHeight(suggestionsRef.current.offsetHeight);
        }
        fetchSuggestions(selectedTarget, tone, true);
      }
    }
  };

  const handleSuggestionClick = (text: string) => {
    onSelectSuggestion(text);
    // Reset state after selection
    setSelectedTarget(null);
    setSelectedTone(null);
    setSuggestions([]);
    setIsExpanded(false);
    // Clear cache so next suggestions reflect updated game context
    suggestionsCache.current = {};
  };

  const handleRefresh = () => {
    if (selectedTone) {
      fetchSuggestions(selectedTarget, selectedTone, true);
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
          <MessageCircle className="toggle-emoji" size={18} />
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
            className={`target-btn target-btn-table ${selectedTarget === 'table' ? 'selected' : ''}`}
            onClick={() => handleTargetSelect('table')}
            title="Talk to the table"
          >
            <Users size={22} style={{ opacity: 0.85 }} />
            <span className="target-name">Table</span>
          </button>
          {aiPlayers.map((player) => {
            // Encode avatar URL path segments to handle spaces in player names
            const rawPath = (typeof player.avatar_url === 'string' && player.avatar_url.length > 0)
              ? player.avatar_url
              : `/api/avatar/${player.name}/confident/full`;
            const encodedPath = rawPath.split('/').map(seg => encodeURIComponent(seg)).join('/');
            const avatarUrl = `${config.API_URL}${encodedPath}`;
            return (
              <button
                key={player.name}
                className={`target-btn target-btn-player ${selectedTarget === player.name ? 'selected' : ''} has-bg-image`}
                onClick={() => handleTargetSelect(player.name)}
                title={`Talk to ${player.name}`}
                style={{ backgroundImage: `url(${avatarUrl})` }}
              >
                <span className="target-name">
                  {player.nickname || player.name}
                </span>
              </button>
            );
          })}
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
              <tone.icon className="tone-icon" size={16} />
              <span className="tone-label">{tone.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Suggestions display */}
      {(loading || suggestions.length > 0) && (
        <div className="suggestions-section">
          <div className="suggestions-header">
            <div className="selector-label">Say:</div>
            <div className="modifier-toggles">
              <div className="toggle-group">
                <button
                  className={`toggle-btn ${length === 'short' ? 'active' : ''}`}
                  onClick={() => setLength('short')}
                >
                  Short
                </button>
                <button
                  className={`toggle-btn ${length === 'long' ? 'active' : ''}`}
                  onClick={() => setLength('long')}
                >
                  Long
                </button>
              </div>
              <div className="toggle-group">
                <button
                  className={`toggle-btn ${intensity === 'chill' ? 'active' : ''}`}
                  onClick={() => setIntensity('chill')}
                >
                  Chill
                </button>
                <button
                  className={`toggle-btn ${intensity === 'spicy' ? 'active' : ''}`}
                  onClick={() => setIntensity('spicy')}
                >
                  🌶️
                </button>
              </div>
            </div>
            <button
              className="refresh-btn"
              onPointerDown={(e) => {
                e.preventDefault();
                if (!loading) handleRefresh();
              }}
              disabled={loading}
              tabIndex={-1}
            >
              ↻
            </button>
          </div>
          <div
            ref={suggestionsRef}
            className="suggestions-container"
            style={containerHeight ? { height: containerHeight } : undefined}
          >
            {loading ? (
              <div className="suggestion-loading">
                <span className="loading-dots">Thinking</span>
              </div>
            ) : (
              suggestions.map((suggestion, index) => (
                <button
                  key={index}
                  className={`suggestion-pill tone-${suggestion.tone}`}
                  onClick={() => handleSuggestionClick(suggestion.text)}
                >
                  {suggestion.text}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
