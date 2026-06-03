import { useState, useCallback, useEffect, useRef } from 'react';
import {
  MessageCircle,
  Flame,
  Award,
  Crosshair,
  Zap,
  Sparkles,
  Handshake,
  type LucideIcon,
} from 'lucide-react';
import type { Player } from '../../types';
import type { ChatTone, ChatLength, ChatIntensity, TargetedSuggestion } from '../../types/chat';
import { gameAPI } from '../../utils/api';
import { logger } from '../../utils/logger';
import { safeGetItem, safeSetItem } from '../../utils/storage';
import { ChatTargetSelector } from './ChatTargetSelector';
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
  /**
   * Receives the suggestion text plus the explicit addressing list when a
   * specific opponent (not "table") is targeted. Drives the backend's
   * find_callouts detection for AI players.
   *
   * Also forwards the structured `tone` and `intensity` selected by the
   * user — the backend uses these to map the message to a
   * `RelationshipEvent` and update bilateral affinity axes. The fields
   * are advisory: callers that don't care can ignore them.
   */
  onSelectSuggestion: (
    text: string,
    addressing?: string[],
    tone?: ChatTone,
    intensity?: ChatIntensity
  ) => void;
  defaultExpanded?: boolean;
  hideHeader?: boolean;
  onSuggestionsLoaded?: () => void;
  guestChatDisabled?: boolean;
  /** Pre-select a player as the target on mount (e.g. opened from dossier). */
  initialTarget?: string | null;
}

interface ToneOption {
  id: ChatTone;
  icon: LucideIcon;
  label: string;
}

// The six mid-hand intents, each keyed off a *different* recipient trait
// (poise / ego / heat / respect / vanity / likability) so the set reads as a
// toolkit. `intimidate` and `dare` are emotional-layer tones (they move the
// target's composure/confidence, not the relationship axes). The retired
// hostile near-duplicates (tilt/goad/needle/bait) are folded into
// `trash_talk`; `bluff` is parked (no target effect). All retain ChatTone
// entries + YAML prompts so any can be restored by re-adding an entry here.
// See docs/plans/SOCIAL_TEMPERAMENT_AND_QUICKCHATS.md §5.
const TONE_OPTIONS: ToneOption[] = [
  { id: 'intimidate', icon: Crosshair, label: 'Intimidate' },
  { id: 'dare', icon: Zap, label: 'Dare' },
  { id: 'trash_talk', icon: Flame, label: 'Trash Talk' },
  { id: 'props', icon: Award, label: 'Props' },
  { id: 'flatter', icon: Sparkles, label: 'Flatter' },
  { id: 'befriend', icon: Handshake, label: 'Befriend' },
];

// Tones that offer the `sarcastic` register — the relationship tones with a
// surface to invert that ride the fixed tone→event dispatch path. Sarcasm
// flips its effect by tone: trash_talk → banter (soften), props → backhand
// (sharpen). The emotional-layer tones (intimidate/dare) have no surface to
// read literally; `flatter` (its valence-flipping path needs separate surgery)
// and `befriend` (passive-aggressive variant) are sincere-only for now —
// both deferred follow-ups.
const SARCASM_ABLE_TONES: ReadonlySet<ChatTone> = new Set<ChatTone>([
  'trash_talk',
  'props',
]);

function toneTakesSarcasm(tone: ChatTone | null): boolean {
  return tone !== null && SARCASM_ABLE_TONES.has(tone);
}

// Delivery register (chill/spicy/sarcastic) is remembered per tone — last
// used wins. So a player who always trash-talks spicy but gives props chill
// doesn't re-toggle each time: picking a tone recalls how they last
// delivered it. Stored as a { tone: register } map; a tone with no stored
// preference cold-starts at 'chill'.
const REGISTER_PREFS_KEY = 'quickchat_register_by_tone';
const DEFAULT_REGISTER: ChatIntensity = 'chill';
// Recognized delivery registers. Stored prefs are validated against this so a
// corrupted / hand-edited / stale-schema value falls back to the default
// rather than flowing through to the API as an unknown register. `sarcastic`
// is additionally gated per-tone at recall time (only sarcasm-able tones may
// resolve to it — see handleToneSelect).
const VALID_REGISTERS: readonly ChatIntensity[] = ['chill', 'spicy', 'sarcastic'];

// Display metadata for the delivery registers. Each carries an emoji (quick
// visual ID), a spelled-out label (so no option is emoji-only and ambiguous),
// and a one-line hint shown live beneath the row — the hint is how we teach
// what each register actually does, especially `sarcastic`, on mobile where
// :hover tooltips never fire.
const REGISTER_META: Record<ChatIntensity, { emoji: string; label: string; hint: string }> = {
  chill: { emoji: '😌', label: 'Chill', hint: 'Playful and light — soften the blow.' },
  spicy: { emoji: '🌶️', label: 'Spicy', hint: 'No filter. Cut deep.' },
  sarcastic: {
    emoji: '😏',
    label: 'Sarcastic',
    hint: "Dry — say the opposite. They won't read it literally.",
  },
};

function readRegisterPrefs(): Partial<Record<ChatTone, ChatIntensity>> {
  const raw = safeGetItem(REGISTER_PREFS_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return {};
    // Keep only recognized register values; drop anything unknown.
    const clean: Partial<Record<ChatTone, ChatIntensity>> = {};
    for (const [tone, register] of Object.entries(parsed)) {
      if (VALID_REGISTERS.includes(register as ChatIntensity)) {
        clean[tone as ChatTone] = register as ChatIntensity;
      }
    }
    return clean;
  } catch {
    return {};
  }
}

function writeRegisterPref(tone: ChatTone, register: ChatIntensity): void {
  const prefs = readRegisterPrefs();
  prefs[tone] = register;
  safeSetItem(REGISTER_PREFS_KEY, JSON.stringify(prefs));
}

export function QuickChatSuggestions({
  gameId,
  playerName,
  players,
  lastAction,
  onSelectSuggestion,
  defaultExpanded = false,
  hideHeader = false,
  onSuggestionsLoaded,
  guestChatDisabled = false,
  initialTarget = null,
}: QuickChatSuggestionsProps) {
  const [selectedTarget, setSelectedTarget] = useState<string | null>(initialTarget);
  const [selectedTone, setSelectedTone] = useState<ChatTone | null>(null);
  const [suggestions, setSuggestions] = useState<TargetedSuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const lastFetchTimeRef = useRef(0);
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  const [containerHeight, setContainerHeight] = useState<number | null>(null);
  const suggestionsRef = useRef<HTMLDivElement>(null);

  // Cache suggestions by target+tone+length+intensity combination
  const suggestionsCache = useRef<Record<string, TargetedSuggestion[]>>({});

  // Length toggle persists globally. Delivery register (intensity) is
  // per-tone instead (see writeRegisterPref) — it's seeded on tone
  // select, so the pre-tone value here is just a harmless default.
  const [length, setLength] = useState<ChatLength>(
    () => (localStorage.getItem('quickchat_length') as ChatLength) || 'short'
  );
  const [intensity, setIntensity] = useState<ChatIntensity>(DEFAULT_REGISTER);

  // Helper to generate cache key
  const getCacheKey = useCallback(
    (target: string | null, tone: ChatTone, len: ChatLength, int: ChatIntensity) =>
      `${target || 'table'}_${tone}_${len}_${int}`,
    []
  );

  // Persist the length toggle globally. (Intensity is persisted per-tone
  // at selection time, not globally — see selectIntensity.)
  useEffect(() => {
    localStorage.setItem('quickchat_length', length);
  }, [length]);

  // All AI players stay selectable as chat targets — including folded ones.
  // Folded targets are still useful: the player can needle a busted opponent
  // or reach out to a friend who just mucked. Folded state is reflected
  // visually so the affordance is honest about who's still in the hand.
  const aiPlayers = players.filter((p) => !p.is_human);
  const fetchSuggestions = useCallback(
    async (target: string | null, tone: ChatTone, forceRefresh = false) => {
      // Block fetching when guest chat is disabled
      if (guestChatDisabled) return;

      // Cooldown check (skip if force refresh)
      const now = Date.now();
      if (!forceRefresh && now - lastFetchTimeRef.current < SUGGESTION_FETCH_COOLDOWN_MS) {
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
          logger.warn('[QuickChat] Using fallback suggestions! API error:', response.error);
        }
        const newSuggestions = response.suggestions || [];
        setSuggestions(newSuggestions);
        // Cache the suggestions
        const cacheKey = getCacheKey(target, tone, length, intensity);
        suggestionsCache.current[cacheKey] = newSuggestions;
        lastFetchTimeRef.current = now;
      } catch (error) {
        logger.error('[QuickChat] Failed to fetch suggestions:', error);
        // Set fallback suggestions
        setSuggestions([
          { text: 'Nice play!', tone },
          { text: 'Interesting...', tone },
        ]);
      } finally {
        setLoading(false);
        setContainerHeight(null); // Release fixed height
        onSuggestionsLoaded?.();
      }
    },
    [
      gameId,
      playerName,
      lastAction,
      length,
      intensity,
      getCacheKey,
      onSuggestionsLoaded,
      guestChatDisabled,
    ]
  );

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
  }, [
    length,
    intensity,
    selectedTarget,
    selectedTone,
    getCacheKey,
    fetchSuggestions,
    onSuggestionsLoaded,
  ]);

  const handleTargetSelect = (target: string | null) => {
    setSelectedTarget(target);
    // If no tone selected yet, clear suggestions and wait
    if (!selectedTone) {
      setSuggestions([]);
    }
    // The useEffect handles fetching/cache lookup when both target and tone are set
  };

  const handleToneSelect = (tone: ChatTone) => {
    setSelectedTone(tone);
    // Recall how this tone was last delivered (last-used register), so the
    // delivery row reflects the player's habit for this intent. Falls back
    // to 'chill' for a tone never delivered before. Guard: if a stale/hand-
    // edited pref resolves to 'sarcastic' on a tone that can't take it
    // (the emotional-layer tones), coerce to 'spicy' so the invalid combo is
    // unreachable — the delivery row never shows a sarcastic that the tone
    // doesn't support.
    const recalled = readRegisterPrefs()[tone] ?? DEFAULT_REGISTER;
    const resolved =
      recalled === 'sarcastic' && !toneTakesSarcasm(tone) ? 'spicy' : recalled;
    setIntensity(resolved);
    // The useEffect handles fetching/cache lookup when both target and tone are set
  };

  // Change the delivery register and remember it for the current tone, so
  // the next time this tone is picked it comes back the same way.
  const selectIntensity = useCallback(
    (register: ChatIntensity) => {
      setIntensity(register);
      if (selectedTone) {
        writeRegisterPref(selectedTone, register);
      }
    },
    [selectedTone]
  );

  const handleSuggestionClick = (text: string) => {
    // Only attach addressing when targeting a specific opponent. The
    // "table" pseudo-target is broadcast chat and should leave the
    // addressing list empty so AIs don't treat it as a direct callout.
    const addressing = selectedTarget && selectedTarget !== 'table' ? [selectedTarget] : undefined;
    // Forward the user's structured tone choice and intensity so the
    // backend can map this message to a RelationshipEvent. Both are
    // null-safe — selectedTone is guaranteed truthy at click time (the
    // suggestion list only renders after a tone is picked).
    onSelectSuggestion(text, addressing, selectedTone ?? undefined, intensity);
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

  // Registers offered for the current tone: chill/spicy always, sarcastic
  // only where the tone has a surface to invert.
  const availableRegisters: ChatIntensity[] = toneTakesSarcasm(selectedTone)
    ? ['chill', 'spicy', 'sarcastic']
    : ['chill', 'spicy'];

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
      <ChatTargetSelector
        aiPlayers={aiPlayers}
        selectedTarget={selectedTarget}
        onTargetSelect={handleTargetSelect}
      />

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

      {/* Delivery register — sits directly below the tone it modifies, and
          appears as soon as a tone is picked. Remembered per-tone. Sarcastic
          is offered only on tones with a surface to invert (warm/hostile
          relationship tones), where its effect flips: banter (trash talk),
          backhand (props). */}
      {selectedTone && (
        <div className="delivery-selector">
          <div className="selector-label">Delivery?</div>
          <div className="toggle-group toggle-group-wide">
            {availableRegisters.map((register) => {
              const meta = REGISTER_META[register];
              return (
                <button
                  key={register}
                  className={`toggle-btn toggle-btn-lg ${intensity === register ? 'active' : ''}`}
                  onClick={() => selectIntensity(register)}
                  title={`${meta.label} — ${meta.hint}`}
                >
                  <span className="toggle-btn-emoji">{meta.emoji}</span>
                  <span className="toggle-btn-text">{meta.label}</span>
                </button>
              );
            })}
          </div>
          {/* Live hint: teaches what the selected register does (esp. sarcastic,
              the novel one) — the mobile-friendly stand-in for a tooltip. */}
          <div className="delivery-hint">{REGISTER_META[intensity].hint}</div>
        </div>
      )}

      {/* Suggestions display */}
      {(loading || suggestions.length > 0) && (
        <div className="suggestions-section">
          <div className="suggestions-header">
            <div className="selector-label">Say:</div>
            <div className="modifier-toggles">
              <div className="toggle-group">
                <button
                  className={`toggle-btn toggle-btn-lg ${length === 'short' ? 'active' : ''}`}
                  onClick={() => setLength('short')}
                  title="Short — a quick one-liner"
                >
                  Short
                </button>
                <button
                  className={`toggle-btn toggle-btn-lg ${length === 'long' ? 'active' : ''}`}
                  onClick={() => setLength('long')}
                  title="Long — a fuller dig"
                >
                  Long
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
            {guestChatDisabled ? (
              <div className="suggestion-disabled-notice">Chat available next turn</div>
            ) : loading ? (
              <div className="suggestion-loading">
                <span className="loading-dots">Thinking</span>
              </div>
            ) : (
              suggestions.map((suggestion, index) => (
                <button
                  key={index}
                  className={`suggestion-pill tone-${suggestion.tone}`}
                  onClick={() => handleSuggestionClick(suggestion.text)}
                  disabled={guestChatDisabled}
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
