import { config } from '../config';
import type {
  ChatTone,
  ChatLength,
  ChatIntensity,
  TargetedSuggestionsResponse,
  PostRoundTone,
  PostRoundSuggestionsResponse,
} from '../types/chat';
import { isOnDeviceLLMAvailable, suggestChatOnDevice } from './onDeviceLLM';

// Common fetch options to ensure credentials are included
const fetchOptions: RequestInit = {
  credentials: 'include',
};

/**
 * Authenticated fetch wrapper for admin endpoints.
 * Includes credentials for session-based auth.
 */
export async function adminFetch(endpoint: string, options: RequestInit = {}): Promise<Response> {
  const headers: HeadersInit = {
    ...(options.headers as Record<string, string>),
  };

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

  fastForward: async (gameId: string, enabled = true) => {
    const response = await fetch(`${config.API_URL}/api/game/${gameId}/fast-forward`, {
      ...fetchOptions,
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    if (!response.ok) {
      throw new Error('Failed to toggle fast-forward');
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
    const response = await fetch(
      `${config.API_URL}/api/game/${gameId}/pressure-stats`,
      fetchOptions
    );

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
    // On-device first (iOS spike, opt-in): generate via Apple Foundation Models.
    // Any unavailability or error falls through to the server route below.
    if (await isOnDeviceLLMAvailable()) {
      try {
        const action = lastAction
          ? `${lastAction.player} just ${lastAction.type}${lastAction.amount ? ` ${lastAction.amount}` : ''}.`
          : '';
        const target = targetPlayer ? `aimed at ${targetPlayer}` : 'about your own hand';
        const prompt =
          `You are ${playerName} at a poker table. Write quick-chat lines ${target}, ` +
          `tone "${tone}", ${length} length, intensity ${intensity}. ${action}`.trim();
        const suggestions = await suggestChatOnDevice(prompt, [tone]);
        return {
          suggestions: suggestions.map((s) => ({ text: s.text, tone })),
          targetPlayer,
          fallback: false,
        };
      } catch {
        // fall through to server
      }
    }

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
    tone: PostRoundTone,
    intensity?: ChatIntensity
  ): Promise<PostRoundSuggestionsResponse> => {
    // On-device first (iOS spike, opt-in): generate via Apple Foundation Models.
    // Any unavailability or error falls through to the server route below.
    if (await isOnDeviceLLMAvailable()) {
      try {
        const prompt =
          `You are ${playerName}, reacting right after a poker hand ended. ` +
          `Write short reactions in tone "${tone}"` +
          `${intensity ? `, intensity ${intensity}` : ''}. Under 10 words each.`;
        const suggestions = await suggestChatOnDevice(prompt, [tone]);
        return {
          suggestions: suggestions.map((s) => ({ text: s.text, tone })),
          fallback: false,
        };
      } catch {
        // fall through to server
      }
    }

    // Backend derives hand context from RecordedHand — we send playerName,
    // tone, and (for the warm tones) the optional sarcastic register.
    const response = await fetch(
      `${config.API_URL}/api/game/${gameId}/post-round-chat-suggestions`,
      {
        ...fetchOptions,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          playerName,
          tone,
          ...(intensity ? { intensity } : {}),
        }),
      }
    );

    if (!response.ok) {
      throw new Error('Failed to fetch post-round chat suggestions');
    }

    return response.json();
  },
};
