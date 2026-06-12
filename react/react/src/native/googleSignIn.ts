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
  // IMPORTANT: do NOT pass `clientId` here. The plugin uses
  // `call.getString("clientId") ?? <iosClientId from capacitor.config>`, so
  // passing the web client id would override the iOS client and Google rejects
  // the native flow with invalid_client. Omitting it lets the plugin use the
  // `iosClientId` (iOS) / `androidClientId` (Android) from capacitor.config.
  GoogleAuth.initialize({
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
