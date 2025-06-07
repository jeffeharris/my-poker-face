import { config } from '../config';

export const gameAPI = {
  createGame: async (playerName: string) => {
    const response = await fetch(`${config.API_URL}/api/new-game`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ playerName }),
    });
    
    if (!response.ok) {
      throw new Error('Failed to create game');
    }
    
    return response.json();
  },

  loadGame: async (gameId: string) => {
    const response = await fetch(`${config.API_URL}/api/game-state/${gameId}`);
    
    if (!response.ok) {
      throw new Error('Failed to load game');
    }
    
    return response.json();
  },

  sendAction: async (gameId: string, action: string, amount?: number) => {
    const response = await fetch(`${config.API_URL}/api/game/${gameId}/action`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        action,
        amount: amount || 0,
      }),
    });
    
    if (!response.ok) {
      throw new Error('Failed to send action');
    }
    
    return response.json();
  },

  sendMessage: async (gameId: string, message: string, sender: string) => {
    const response = await fetch(`${config.API_URL}/api/game/${gameId}/message`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message,
        sender,
      }),
    });
    
    if (!response.ok) {
      throw new Error('Failed to send message');
    }
    
    return response.json();
  },

  getPressureStats: async (gameId: string) => {
    const response = await fetch(`${config.API_URL}/api/game/${gameId}/pressure-stats`);
    
    if (!response.ok) {
      throw new Error('Failed to fetch pressure stats');
    }
    
    return response.json();
  },
};