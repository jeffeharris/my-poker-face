// Configuration for the React app
export const config = {
  // Backend API URL - can be overridden by environment variable
  // In production, use relative URLs to work with the same origin
  API_URL: import.meta.env.VITE_API_URL || (import.meta.env.PROD ? '' : 'http://localhost:5001'),
  
  // WebSocket URL - defaults to same as API
  // In production, use the current origin for WebSocket connections
  SOCKET_URL: import.meta.env.VITE_SOCKET_URL || (import.meta.env.PROD ? window.location.origin : 'http://localhost:5001'),
  
  // Debug mode - shows debug panel when enabled
  ENABLE_DEBUG: import.meta.env.VITE_ENABLE_DEBUG === 'true' || false,
  
  // Chat Phase 2 Feature Flags
  CHAT_FEATURES: {
    // Enable AI-powered quick chat suggestions
    QUICK_SUGGESTIONS: import.meta.env.VITE_ENABLE_QUICK_CHAT === 'true' || false,
    
    // Enable player-specific message filtering
    PLAYER_FILTER: import.meta.env.VITE_ENABLE_PLAYER_FILTER === 'true' || false,
    
    // Enable message grouping for consecutive messages
    MESSAGE_GROUPING: import.meta.env.VITE_ENABLE_MESSAGE_GROUPING === 'true' || false,
    
    // Enable special event indicators (wins, all-ins, etc.)
    EVENT_INDICATORS: import.meta.env.VITE_ENABLE_EVENT_INDICATORS === 'true' || false
  },
  
  // Betting UI Feature Flags
  BETTING_FEATURES: {
    // Enable smart bet suggestions (C-Bet, Value, Overbet)
    SMART_BET_SUGGESTIONS: import.meta.env.VITE_ENABLE_SMART_BETS === 'true' || false
  }
};