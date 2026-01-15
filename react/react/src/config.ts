// Configuration for the React app
export const config = {
  // Backend API URL - can be overridden by environment variable
  // In production, use relative URLs to work with the same origin
  API_URL: import.meta.env.VITE_API_URL || (import.meta.env.PROD ? '' : `http://${window.location.hostname}:${import.meta.env.VITE_BACKEND_PORT || '5000'}`),

  // WebSocket URL - defaults to same as API
  // In production, use the current origin for WebSocket connections
  SOCKET_URL: import.meta.env.VITE_SOCKET_URL || (import.meta.env.PROD ? window.location.origin : `http://${window.location.hostname}:${import.meta.env.VITE_BACKEND_PORT || '5000'}`),
  
  // Debug mode - shows debug panel when enabled
  ENABLE_DEBUG: import.meta.env.VITE_ENABLE_DEBUG === 'true' || false,

  // AI Debug mode - shows LLM model info when clicking AI player cards
  ENABLE_AI_DEBUG: import.meta.env.VITE_ENABLE_AI_DEBUG === 'true' || false
};