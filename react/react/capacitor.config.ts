import type { CapacitorConfig } from '@capacitor/cli';
import { KeyboardResize, KeyboardStyle } from '@capacitor/keyboard';

/**
 * Capacitor native shell config (iOS / Android).
 *
 * This file is consumed only by the Capacitor CLI (`cap sync` / `cap open`) — it
 * is not part of the Vite/tsc build, so the app bundle is unchanged.
 *
 * IMPORTANT: the native WebView's own origin is `capacitor://localhost`, not the
 * API server. Build the native bundle with the API origin pinned, e.g.:
 *   VITE_API_URL=https://mypokerfacegame.com \
 *   VITE_SOCKET_URL=https://mypokerfacegame.com \
 *   npm run build && npx cap sync
 * Auth rides the bearer header (not cookies), so cross-origin is fine — just
 * ensure the API's CORS allows the WebView origin.
 */
const config: CapacitorConfig = {
  appId: 'com.mypokerface.app',
  appName: 'My Poker Face',
  webDir: 'dist',
  ios: {
    // The native scroll view owns the safe-area insets: 'always' pushes ALL web
    // content below the notch / status bar / home indicator automatically, so
    // components don't each have to opt into env(safe-area-inset-*) padding.
    // (We tried 'never' + viewport-fit=cover to let CSS own the insets, but top-
    // anchored elements without safe-area padding — the back button, avatar menu —
    // slid under the status bar. Revisit only with a full safe-area CSS pass.)
    // backgroundColor keeps the inset strip behind the status bar dark, not white.
    contentInset: 'always',
    backgroundColor: '#0a0b10',
  },
  plugins: {
    // Keyboard: Native resize shrinks the WebView viewport when the keyboard opens,
    // so the fixed bottom chat sheet (position:fixed; max-height:82dvh) rides above
    // the keyboard instead of being covered. Dark style matches the app. The input
    // accessory bar (prev/next/Done toolbar) is hidden at runtime in
    // src/native/bootstrap.ts — that also silences the benign _UIButtonBarButton
    // Auto Layout warning that bar emits.
    Keyboard: {
      resize: KeyboardResize.Native,
      style: KeyboardStyle.Dark,
    },
    // @codetrix-studio/capacitor-google-auth reads the client IDs from HERE on
    // native (it does NOT read Info.plist's GIDClientID). These are public OAuth
    // client IDs (not secrets). iosClientId → the ID token's `aud` on iOS;
    // serverClientId → the web client, used for the server auth code.
    GoogleAuth: {
      iosClientId: '637263623890-j2858ch2v6vdr0nr2pv0q1jmnj6nf108.apps.googleusercontent.com',
      serverClientId:
        '637263623890-93qcchki4jp5ll80t0sra22b4qpjbit7.apps.googleusercontent.com',
      scopes: ['profile', 'email'],
      forceCodeForRefreshToken: false,
    },
  },
};

export default config;
