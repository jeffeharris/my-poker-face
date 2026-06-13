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
  suggestChat(options: { prompt: string; system?: string; tones?: string[] }): Promise<{
    suggestions: Array<{ text: string; tone: string }>;
  }>;
  prewarm(): Promise<{ warmed: boolean }>;
}

const FoundationModels = registerPlugin<FoundationModelsPlugin>('FoundationModels');

const FLAG_KEY = 'onDeviceLLM';

export interface ChatSuggestion {
  text: string;
  tone: string;
}

/**
 * On-device is ON by default whenever the model is available. The flag is now
 * only a kill switch: set `localStorage.onDeviceLLM = '0'` to force the server
 * route (for A/B comparison). Any other value (or unset) = on-device preferred.
 */
function isKillSwitched(): boolean {
  try {
    return localStorage.getItem(FLAG_KEY) === '0';
  } catch {
    return false;
  }
}

// Cache the (stable) availability check so we don't probe the bridge on every keystroke.
let availabilityPromise: Promise<boolean> | null = null;

/**
 * Whether on-device suggestion generation should be used. On by default in the
 * native shell when the model reports itself available; the kill switch forces off.
 */
export async function isOnDeviceLLMAvailable(): Promise<boolean> {
  if (isKillSwitched() || !isNativePlatform()) return false;
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
 *
 * `system` (server-composes parity) overrides the plugin's generic instructions
 * with the server's exact prompt. `count` caps the result to match the server UI.
 */
export async function suggestChatOnDevice(opts: {
  prompt: string;
  system?: string;
  tones?: string[];
  count?: number;
}): Promise<ChatSuggestion[]> {
  const { prompt, system, tones, count } = opts;
  const { suggestions } = await FoundationModels.suggestChat({ prompt, system, tones });
  if (!Array.isArray(suggestions) || suggestions.length === 0) {
    throw new Error('on-device model returned no suggestions');
  }
  return typeof count === 'number' ? suggestions.slice(0, count) : suggestions;
}

/**
 * Load the on-device model ahead of a request — call when the chat options are
 * presented so the first suggestion doesn't pay cold model-load latency.
 *
 * Deliberately NOT gated on `isOnDeviceLLMAvailable()`: prewarm's job is to *make*
 * the model available. On Android the model is `DOWNLOADABLE` on first run and
 * `prewarm()` kicks off the one-time download — gating on availability would be a
 * catch-22 (model only downloads via prewarm, prewarm only runs once available), so
 * it would never download and on-device would never engage. On iOS, prewarming when
 * the model isn't available is a safe no-op (the OS manages the model download).
 * Best-effort and cheap to call repeatedly.
 */
export async function prewarmOnDevice(): Promise<void> {
  if (isKillSwitched() || !isNativePlatform()) return;
  try {
    await FoundationModels.prewarm();
  } catch {
    // best-effort — generation still works (or falls back), just colder
  }
}
