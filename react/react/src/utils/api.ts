import { config } from '../config';
import type { ChatTone, ChatLength, ChatIntensity, TargetedSuggestionsResponse, PostRoundTone, PostRoundSuggestionsResponse } from '../types/chat';

// Common fetch options to ensure credentials are included
const fetchOptions: RequestInit = {
  credentials: 'include',
};

export const gameAPI = {
  createGame: async (playerName: string) => {
    const response = await fetch(`${config.API_URL}/api/new-game`, {
      ...fetchOptions,
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
    const response = await fetch(`${config.API_URL}/api/game-state/${gameId}`, fetchOptions);
    
    if (!response.ok) {
      throw new Error('Failed to load game');
    }
    
    return response.json();
  },

  sendAction: async (gameId: string, action: string, amount?: number) => {
    const response = await fetch(`${config.API_URL}/api/game/${gameId}/action`, {
      ...fetchOptions,
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
      ...fetchOptions,
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
    const response = await fetch(`${config.API_URL}/api/game/${gameId}/pressure-stats`, fetchOptions);

    if (!response.ok) {
      throw new Error('Failed to fetch pressure stats');
    }

    return response.json();
  },

  getTargetedChatSuggestions: async (
    gameId: string,
    playerName: string,
    targetPlayer: string | null,
    tone: ChatTone,
    length: ChatLength,
    intensity: ChatIntensity,
    lastAction?: { type: string; player: string; amount?: number }
  ): Promise<TargetedSuggestionsResponse> => {
    const response = await fetch(`${config.API_URL}/api/game/${gameId}/targeted-chat-suggestions`, {
      ...fetchOptions,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        playerName,
        targetPlayer,
        tone,
        length,
        intensity,
        lastAction,
      }),
    });

    if (!response.ok) {
      throw new Error('Failed to fetch chat suggestions');
    }

    return response.json();
  },

  getPostRoundChatSuggestions: async (
    gameId: string,
    playerName: string,
    tone: PostRoundTone
  ): Promise<PostRoundSuggestionsResponse> => {
    // Backend now derives all context from RecordedHand - only need playerName and tone
    const response = await fetch(`${config.API_URL}/api/game/${gameId}/post-round-chat-suggestions`, {
      ...fetchOptions,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        playerName,
        tone,
      }),
    });

    if (!response.ok) {
      throw new Error('Failed to fetch post-round chat suggestions');
    }

    return response.json();
  },
};