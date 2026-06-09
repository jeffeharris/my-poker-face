import { isNativePlatform } from '../utils/nativeAuth';

/**
 * Native Google sign-in via @codetrix-studio/capacitor-google-auth.
 *
 * The plugin runs the platform's native Google flow (no embedded WebView, which
 * Google blocks) and returns an ID token. We hand that token to
 * `useAuth.loginWithGoogleNative()`, which exchanges it for our JWT pair at
 * `POST /api/auth/google/native`.
 *
 * The plugin is dynamically imported so it never enters the web bundle.
 */

let initialized = false;

export async function initGoogleAuth(): Promise<void> {
  if (!isNativePlatform() || initialized) return;
  const { GoogleAuth } = await import('@codetrix-studio/capacitor-google-auth');
  GoogleAuth.initialize({
    // Web/server client ID — drives the ID token `aud` on Android. iOS reads its
    // own client ID from Info.plist (GIDClientID + reversed-id URL scheme).
    clientId: import.meta.env.VITE_GOOGLE_CLIENT_ID,
    scopes: ['profile', 'email'],
    grantOfflineAccess: false,
  });
  initialized = true;
}

/** Run the native Google flow and return the Google ID token. */
export async function signInWithGoogleNative(): Promise<string> {
  await initGoogleAuth();
  const { GoogleAuth } = await import('@codetrix-studio/capacitor-google-auth');
  const user = await GoogleAuth.signIn();
  const idToken = user?.authentication?.idToken;
  if (!idToken) {
    throw new Error('Google sign-in returned no ID token');
  }
  return idToken;
}
