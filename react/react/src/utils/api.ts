import { config } from '../config';
import type { ChatTone, ChatLength, ChatIntensity, TargetedSuggestionsResponse, PostRoundTone, PostRoundSuggestionsResponse } from '../types/chat';

// Common fetch options to ensure credentials are included
const fetchOptions: RequestInit = {
  credentials: 'include',
};

// Admin token storage key
const ADMIN_TOKEN_KEY = 'admin_token';

/**
 * Get the admin token from URL (first priority) or localStorage (fallback).
 * If found in URL, it's automatically persisted to localStorage.
 */
export function getAdminToken(): string | null {
  // First check URL for token (allows override/initial auth)
  const params = new URLSearchParams(window.location.search);
  const urlToken = params.get('admin_token');

  if (urlToken) {
    // Store in localStorage for persistence across navigation
    localStorage.setItem(ADMIN_TOKEN_KEY, urlToken);
    return urlToken;
  }

  // Fall back to localStorage
  return localStorage.getItem(ADMIN_TOKEN_KEY);
}

/**
 * Clear the stored admin token (for logout)
 */
export function clearAdminToken(): void {
  localStorage.removeItem(ADMIN_TOKEN_KEY);
}

/**
 * Authenticated fetch wrapper for admin endpoints.
 * Automatically includes the admin token as Bearer auth header.
 */
export async function adminFetch(endpoint: string, options: RequestInit = {}): Promise<Response> {
  const token = getAdminToken();
  const headers: HeadersInit = {
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  // Add Content-Type for JSON if body is present, not already set, and not FormData
  // (FormData needs browser to set Content-Type with boundary automatically)
  const isFormData = options.body instanceof FormData;
  if (options.body && !headers['Content-Type'] && !isFormData) {
    headers['Content-Type'] = 'application/json';
  }

  return fetch(`${config.API_URL}${endpoint}`, {
    ...fetchOptions,
    ...options,
    headers,
  });
}

// Legacy adminAPI object for backward compatibility
export const adminAPI = {
  getToken: getAdminToken,
  clearToken: clearAdminToken,
  fetch: adminFetch,
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