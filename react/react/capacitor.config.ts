import type { CapacitorConfig } from '@capacitor/cli';

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
    contentInset: 'always',
  },
  plugins: {
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
