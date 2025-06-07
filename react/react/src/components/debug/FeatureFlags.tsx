import { useState, useEffect } from 'react';
import { config } from '../../config';

// Feature flag state stored in localStorage for persistence
const FEATURE_FLAGS_KEY = 'poker-chat-features';

export interface ChatFeatureFlags {
  quickSuggestions: boolean;
  playerFilter: boolean;
  messageGrouping: boolean;
  eventIndicators: boolean;
}

// Default to config values or false
const defaultFlags: ChatFeatureFlags = {
  quickSuggestions: config.CHAT_FEATURES.QUICK_SUGGESTIONS,
  playerFilter: config.CHAT_FEATURES.PLAYER_FILTER,
  messageGrouping: config.CHAT_FEATURES.MESSAGE_GROUPING,
  eventIndicators: config.CHAT_FEATURES.EVENT_INDICATORS
};

// Get feature flags from localStorage or defaults
export function getFeatureFlags(): ChatFeatureFlags {
  try {
    const stored = localStorage.getItem(FEATURE_FLAGS_KEY);
    if (stored) {
      return { ...defaultFlags, ...JSON.parse(stored) };
    }
  } catch (e) {
    console.error('Failed to load feature flags:', e);
  }
  return defaultFlags;
}

// Save feature flags to localStorage
export function saveFeatureFlags(flags: ChatFeatureFlags) {
  try {
    localStorage.setItem(FEATURE_FLAGS_KEY, JSON.stringify(flags));
    // Dispatch custom event so other components can react
    window.dispatchEvent(new CustomEvent('featureFlagsChanged', { detail: flags }));
  } catch (e) {
    console.error('Failed to save feature flags:', e);
  }
}

// Hook to use feature flags in components
export function useFeatureFlags() {
  const [flags, setFlags] = useState<ChatFeatureFlags>(getFeatureFlags());

  useEffect(() => {
    const handleFlagsChange = (e: CustomEvent) => {
      setFlags(e.detail);
    };

    window.addEventListener('featureFlagsChanged', handleFlagsChange as EventListener);
    return () => {
      window.removeEventListener('featureFlagsChanged', handleFlagsChange as EventListener);
    };
  }, []);

  return flags;
}

// Feature Flags UI Component
export function FeatureFlags() {
  const [flags, setFlags] = useState<ChatFeatureFlags>(getFeatureFlags());
  const [showSaved, setShowSaved] = useState(false);

  const handleToggle = (key: keyof ChatFeatureFlags) => {
    const newFlags = { ...flags, [key]: !flags[key] };
    setFlags(newFlags);
    saveFeatureFlags(newFlags);
    
    // Show saved indicator
    setShowSaved(true);
    setTimeout(() => setShowSaved(false), 2000);
  };

  const resetToDefaults = () => {
    setFlags(defaultFlags);
    saveFeatureFlags(defaultFlags);
    setShowSaved(true);
    setTimeout(() => setShowSaved(false), 2000);
  };

  return (
    <div style={{
      padding: '1rem',
      background: 'rgba(0, 0, 0, 0.5)',
      color: '#fff',
      fontFamily: 'monospace',
      fontSize: '0.875rem'
    }}>
      <div style={{ 
        display: 'flex', 
        justifyContent: 'space-between', 
        alignItems: 'center',
        marginBottom: '1rem' 
      }}>
        <h4 style={{ margin: 0 }}>Chat Feature Flags</h4>
        {showSaved && (
          <span style={{ 
            color: '#4caf50', 
            fontSize: '0.75rem',
            animation: 'fadeIn 0.3s ease-out'
          }}>
            ✓ Saved
          </span>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={flags.quickSuggestions}
            onChange={() => handleToggle('quickSuggestions')}
            style={{ cursor: 'pointer' }}
          />
          <div>
            <div style={{ fontWeight: 'bold' }}>Quick Chat Suggestions</div>
            <div style={{ fontSize: '0.75rem', opacity: 0.7 }}>
              AI-powered context-aware message suggestions
            </div>
          </div>
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={flags.playerFilter}
            onChange={() => handleToggle('playerFilter')}
            style={{ cursor: 'pointer' }}
          />
          <div>
            <div style={{ fontWeight: 'bold' }}>Player Filter Dropdown</div>
            <div style={{ fontSize: '0.75rem', opacity: 0.7 }}>
              Filter messages by specific player
            </div>
          </div>
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={flags.messageGrouping}
            onChange={() => handleToggle('messageGrouping')}
            style={{ cursor: 'pointer' }}
          />
          <div>
            <div style={{ fontWeight: 'bold' }}>Message Grouping</div>
            <div style={{ fontSize: '0.75rem', opacity: 0.7 }}>
              Group consecutive messages from same player
            </div>
          </div>
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={flags.eventIndicators}
            onChange={() => handleToggle('eventIndicators')}
            style={{ cursor: 'pointer' }}
          />
          <div>
            <div style={{ fontWeight: 'bold' }}>Special Event Indicators</div>
            <div style={{ fontSize: '0.75rem', opacity: 0.7 }}>
              Visual indicators for wins, all-ins, etc.
            </div>
          </div>
        </label>
      </div>

      <div style={{ 
        marginTop: '1rem', 
        paddingTop: '1rem', 
        borderTop: '1px solid rgba(255,255,255,0.2)' 
      }}>
        <button
          onClick={resetToDefaults}
          style={{
            padding: '0.25rem 0.5rem',
            background: 'rgba(255,255,255,0.1)',
            border: '1px solid rgba(255,255,255,0.3)',
            color: '#fff',
            borderRadius: '0.25rem',
            cursor: 'pointer',
            fontSize: '0.75rem'
          }}
        >
          Reset to Defaults
        </button>
      </div>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </div>
  );
}