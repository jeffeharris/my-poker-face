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
 * Server-composes parity: the suggestion endpoints accept `render_only: true` and
 * return the EXACT prompt they'd send to the LLM ({ messages, count }) without
 * calling it. The native client runs that prompt on Apple's on-device model, so
 * the content matches the server while the (paid) inference moves to the phone.
 */
interface ComposedPromptResponse {
  messages?: Array<{ role: string; content: string }>;
  count?: number;
}

function parseComposedPrompt(
  payload: ComposedPromptResponse
): { system?: string; user: string; count?: number } | null {
  const messages = payload?.messages;
  if (!Array.isArray(messages)) return null;
  const user = messages.find((m) => m?.role === 'user')?.content;
  if (typeof user !== 'string' || !user) return null;
  const system = messages.find((m) => m?.role === 'system')?.content;
  return { system, user, count: payload?.count };
}

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
    const body = { playerName, targetPlayer, tone, length, intensity, lastAction };

    // On-device first (server-composes parity): ask the server to compose the
    // identical prompt (render_only) and run it on Apple Foundation Models. Any
    // failure falls through to the normal server LLM route below.
    if (await isOnDeviceLLMAvailable()) {
      try {
        const composed = await fetch(
          `${config.API_URL}/api/game/${gameId}/targeted-chat-suggestions`,
          {
            ...fetchOptions,
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...body, render_only: true }),
          }
        );
        if (composed.ok) {
          const payload = await composed.json();
          const parsed = parseComposedPrompt(payload);
          if (parsed) {
            const suggestions = await suggestChatOnDevice({
              prompt: parsed.user,
              system: parsed.system,
              tones: [tone],
              count: parsed.count ?? 2,
            });
            return {
              suggestions: suggestions.map((s) => ({ text: s.text, tone })),
              targetPlayer,
              fallback: false,
            };
          }
          // Backend predates render_only and returned suggestions directly — use them.
          if (Array.isArray(payload?.suggestions)) {
            return payload as TargetedSuggestionsResponse;
          }
        }
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
      body: JSON.stringify(body),
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
    // Backend derives hand context from RecordedHand — we send playerName,
    // tone, and (for the warm tones) the optional sarcastic register.
    const body = { playerName, tone, ...(intensity ? { intensity } : {}) };

    // On-device first (server-composes parity): identical prompt from the server,
    // run on Apple Foundation Models. Falls through to the server LLM on any error.
    if (await isOnDeviceLLMAvailable()) {
      try {
        const composed = await fetch(
          `${config.API_URL}/api/game/${gameId}/post-round-chat-suggestions`,
          {
            ...fetchOptions,
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...body, render_only: true }),
          }
        );
        if (composed.ok) {
          const payload = await composed.json();
          const parsed = parseComposedPrompt(payload);
          if (parsed) {
            const suggestions = await suggestChatOnDevice({
              prompt: parsed.user,
              system: parsed.system,
              tones: [tone],
              count: parsed.count ?? 2,
            });
            return {
              suggestions: suggestions.map((s) => ({ text: s.text, tone })),
              fallback: false,
            };
          }
          if (Array.isArray(payload?.suggestions)) {
            return payload as PostRoundSuggestionsResponse;
          }
        }
      } catch {
        // fall through to server
      }
    }

    const response = await fetch(
      `${config.API_URL}/api/game/${gameId}/post-round-chat-suggestions`,
      {
        ...fetchOptions,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      }
    );

    if (!response.ok) {
      throw new Error('Failed to fetch post-round chat suggestions');
    }

    return response.json();
  },
};
