import { registerPlugin } from '@capacitor/core';
import { isNativePlatform } from './nativeAuth';

/**
 * On-device chat-suggestion generation via Apple's Foundation Models framework
 * (WWDC25, iOS 26+), bridged through the native `FoundationModels` Capacitor plugin
 * (`ios/App/App/FoundationModelsBridgePlugin.swift`).
 *
 * PROOF OF CONCEPT, native-only and best-effort: on the web, on devices without
 * Apple Intelligence, or before the native plugin exists, every entry point reports
 * "unavailable" and callers fall back to the existing server route. Mirrors the
 * native-guard pattern in `widgetData.ts`.
 *
 * Opt-in: gated behind the `onDeviceLLM` localStorage flag so the spike can be
 * A/B'd against the server output without shipping it on by default.
 */

interface FoundationModelsPlugin {
  availability(): Promise<{ available: boolean; reason?: string }>;
  suggestChat(options: { prompt: string; tones?: string[] }): Promise<{
    suggestions: Array<{ text: string; tone: string }>;
  }>;
}

const FoundationModels = registerPlugin<FoundationModelsPlugin>('FoundationModels');

const FLAG_KEY = 'onDeviceLLM';

export interface ChatSuggestion {
  text: string;
  tone: string;
}

/** Opt-in flag — set `localStorage.onDeviceLLM = '1'` to enable the spike. */
function isFlagEnabled(): boolean {
  try {
    return localStorage.getItem(FLAG_KEY) === '1';
  } catch {
    return false;
  }
}

// Cache the (stable) availability check so we don't probe the bridge on every keystroke.
let availabilityPromise: Promise<boolean> | null = null;

/**
 * Whether on-device suggestion generation should be used. True only when the flag
 * is on, we're in the native shell, and the model reports itself available.
 */
export async function isOnDeviceLLMAvailable(): Promise<boolean> {
  if (!isFlagEnabled() || !isNativePlatform()) return false;
  if (!availabilityPromise) {
    availabilityPromise = (async () => {
      try {
        const { available } = await FoundationModels.availability();
        return available;
      } catch {
        // Bridge missing (pre-plugin build / web) — treat as unavailable.
        return false;
      }
    })();
  }
  return availabilityPromise;
}

/**
 * Generate quick-chat suggestions on-device. Throws on any failure so the caller
 * can fall back to the server route; never returns an empty/partial result silently.
 */
export async function suggestChatOnDevice(
  prompt: string,
  tones?: string[],
): Promise<ChatSuggestion[]> {
  const { suggestions } = await FoundationModels.suggestChat({ prompt, tones });
  if (!Array.isArray(suggestions) || suggestions.length === 0) {
    throw new Error('on-device model returned no suggestions');
  }
  return suggestions;
}
