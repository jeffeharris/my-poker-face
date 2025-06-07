// Configuration for the React app
export const config = {
  // Backend API URL - can be overridden by environment variable
  API_URL: import.meta.env.VITE_API_URL || 'http://localhost:5001',
  
  // WebSocket URL - defaults to same as API
  SOCKET_URL: import.meta.env.VITE_SOCKET_URL || import.meta.env.VITE_API_URL || 'http://localhost:5001'
};