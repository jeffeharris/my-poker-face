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

export async function initNative(): Promise<void> {
  await installTokenStorage();
  await initGoogleAuth();
}
