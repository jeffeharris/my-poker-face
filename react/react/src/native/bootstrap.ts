import { configureTokenStorage } from '../utils/nativeAuth';
import { initGoogleAuth } from './googleSignIn';

/**
 * Native (Capacitor) startup wiring. Called once from `main.tsx` — and only on a
 * native platform (see `isNativePlatform`) — before the app renders, so the auth
 * bootstrap finds persisted tokens.
 *
 * Capacitor plugins are dynamically imported here so they never enter the web
 * bundle.
 */

const ACCESS_KEY = 'mpf_access_token';
const REFRESH_KEY = 'mpf_refresh_token';

/**
 * Persist the JWT pair via @capacitor/preferences (encrypted at rest on device).
 * For stronger guarantees, swap in a Keychain/Keystore plugin behind the same
 * TokenStorage interface — this function is the only integration point.
 */
async function installTokenStorage(): Promise<void> {
  const { Preferences } = await import('@capacitor/preferences');
  configureTokenStorage({
    async load() {
      const [a, r] = await Promise.all([
        Preferences.get({ key: ACCESS_KEY }),
        Preferences.get({ key: REFRESH_KEY }),
      ]);
      return {
        accessToken: a.value ?? undefined,
        refreshToken: r.value ?? undefined,
      };
    },
    async save({ accessToken, refreshToken }) {
      await Promise.all([
        Preferences.set({ key: ACCESS_KEY, value: accessToken }),
        Preferences.set({ key: REFRESH_KEY, value: refreshToken }),
      ]);
    },
    async clear() {
      await Promise.all([
        Preferences.remove({ key: ACCESS_KEY }),
        Preferences.remove({ key: REFRESH_KEY }),
      ]);
    },
  });
}

/**
 * Hide the keyboard input accessory bar (the prev/next/Done toolbar above the iOS
 * keyboard). We don't use it, it adds clutter over a dark app, and it's the source
 * of the benign `_UIButtonBarButton` "Unable to simultaneously satisfy constraints"
 * console warning. Resize mode + dark style are set declaratively in
 * capacitor.config.ts; only the accessory bar needs a runtime call.
 */
async function configureKeyboard(): Promise<void> {
  const { Keyboard } = await import('@capacitor/keyboard');
  try {
    await Keyboard.setAccessoryBarVisible({ isVisible: false });
  } catch {
    // Non-fatal (older iOS / unavailable) — never block startup over chrome.
  }
}

export async function initNative(): Promise<void> {
  await installTokenStorage();
  await initGoogleAuth();
  await configureKeyboard();
}
