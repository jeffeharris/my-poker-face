---
purpose: Take the existing React app to a native iOS/Android build (Capacitor) with native Google sign-in against the bearer-token auth backend
type: guide
created: 2026-06-08
last_updated: 2026-06-08
---

# Native (iOS / Android) Setup

This guide covers the remaining, machine-specific steps to ship the existing
React frontend as a native app. The **backend and frontend auth plumbing are
already done** (see "What's already in place"); everything below requires a Mac
with Xcode (iOS) / Android Studio and access to the Google Cloud Console, which
is why it lives in a guide rather than being pre-wired in the repo.

## What's already in place

| Layer | Status |
|---|---|
| `POST /api/auth/google/native` — verify a Google ID token → JWT pair | ✅ `poker/auth.py` |
| `POST /api/auth/token/refresh` — rotating refresh → access token | ✅ `poker/auth.py` |
| Bearer accepted by `get_current_user` + Socket.IO `authenticate_socket` | ✅ |
| Multi-platform audience allowlist (`GOOGLE_ALLOWED_AUDIENCES`) | ✅ `flask_app/config.py` |
| Frontend bearer injection on every API call + 401 refresh-retry | ✅ `src/utils/csrf.ts`, `src/utils/nativeAuth.ts` |
| Socket.IO `auth` payload on native | ✅ `src/utils/socket.ts` (`createAuthedSocket`) |
| `useAuth.loginWithGoogleNative(idToken)` + token load/clear | ✅ `src/hooks/useAuth.tsx` |

The frontend pieces are **inert on web** — they only activate once a token is
held, so the cookie flow is unchanged.

## Remaining steps

### 1. Register per-platform OAuth clients (Google Cloud Console)

Each platform needs its own OAuth client ID; the ID token's `aud` claim differs
per platform, which is why the backend checks an allowlist.

1. Console → APIs & Services → Credentials → Create OAuth client ID.
2. Create an **iOS** client (bundle id e.g. `com.mypokerface.app`) and an
   **Android** client (package name + SHA-1 of your signing key).
3. Set the backend env vars (see `.env.example`):
   ```bash
   GOOGLE_IOS_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_ANDROID_CLIENT_ID=...apps.googleusercontent.com
   ```
   These flow into `GOOGLE_ALLOWED_AUDIENCES` automatically.

### 2. Add Capacitor + generate native projects

From `react/react/`:

```bash
npm install --save @capacitor/core @capacitor/preferences
npm install --save-dev @capacitor/cli
npx cap init "My Poker Face" com.mypokerface.app --web-dir=dist
npm run build                 # produce dist/
npm install --save @capacitor/ios @capacitor/android
npx cap add ios
npx cap add android
npx cap sync
```

Create `react/react/capacitor.config.ts`:

```ts
import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.mypokerface.app',
  appName: 'My Poker Face',
  webDir: 'dist',
  server: {
    // Point the WebView at the deployed API origin so cookies/sockets resolve.
    // (The app calls config.API_URL; set VITE_API_URL at build time instead if
    // you prefer to keep the WebView on the bundled origin.)
    androidScheme: 'https',
  },
};

export default config;
```

> Set `VITE_API_URL=https://mypokerfacegame.com` (and `VITE_SOCKET_URL`) for the
> native build so the WebView talks to production rather than a dev origin.

### 3. Wire native Google sign-in

Pick a plugin (either works with the existing backend):

```bash
npm install --save @codetrix-studio/capacitor-google-auth
# or: npm install --save @capacitor-firebase/authentication firebase
```

Call it from the login screen and hand the ID token to the existing hook:

```ts
import { GoogleAuth } from '@codetrix-studio/capacitor-google-auth';
import { useAuth } from '../hooks/useAuth';

// once at startup: GoogleAuth.initialize({ clientId: IOS_OR_WEB_CLIENT_ID, scopes: ['email'] })

const { loginWithGoogleNative } = useAuth();

async function onTapGoogle() {
  const result = await GoogleAuth.signIn();
  const idToken = result.authentication.idToken;
  const res = await loginWithGoogleNative(idToken); // → /api/auth/google/native
  if (!res.success) showError(res.error);
}
```

`loginWithGoogleNative` stores the returned access + refresh tokens; from then on
every `fetch` and Socket.IO connection is authenticated automatically.

### 4. Wire secure token storage (Keychain / Keystore)

By default tokens live in memory (cold start = re-login). Install a secure-storage
adapter at startup so the session survives restarts. Add to `src/main.tsx` (before
`createRoot`), using `@capacitor/preferences` (encrypted on device):

```ts
import { Preferences } from '@capacitor/preferences';
import { configureTokenStorage, isNativePlatform } from './utils/nativeAuth';

if (isNativePlatform()) {
  configureTokenStorage({
    async load() {
      const [a, r] = await Promise.all([
        Preferences.get({ key: 'mpf_access' }),
        Preferences.get({ key: 'mpf_refresh' }),
      ]);
      return { accessToken: a.value ?? undefined, refreshToken: r.value ?? undefined };
    },
    async save({ accessToken, refreshToken }) {
      await Preferences.set({ key: 'mpf_access', value: accessToken });
      await Preferences.set({ key: 'mpf_refresh', value: refreshToken });
    },
    async clear() {
      await Preferences.remove({ key: 'mpf_access' });
      await Preferences.remove({ key: 'mpf_refresh' });
    },
  });
}
```

> For stronger at-rest protection than Preferences, swap in a Keychain/Keystore
> plugin (e.g. `capacitor-secure-storage-plugin`) behind the same `TokenStorage`
> interface — `configureTokenStorage` is the only integration point.

### 5. Build & run

```bash
npm run build && npx cap sync
npx cap open ios       # → run from Xcode on a simulator/device
npx cap open android   # → run from Android Studio
```

## Notes & gotchas

- **Sign in with Apple**: iOS App Store review requires it when you offer Google
  sign-in. The backend pattern is identical — add an Apple-token-verifying
  endpoint mirroring `/api/auth/google/native` and a second
  `loginWithApple(...)` hook method. Not required to run the app.
- **Access token lifetime**: 1h access / 30d refresh (`poker/auth.py`
  `ACCESS_TOKEN_EXPIRATION` / `REFRESH_TOKEN_EXPIRATION`). The frontend refreshes
  on 401 automatically (single-flight). Tune as needed.
- **Refresh revocation**: refresh is currently stateless (rotated, not tracked
  server-side). If you need server-side revoke/lockout, persist issued refresh
  token ids and check them in `/api/auth/token/refresh`.
- **CORS / cookies**: native runs cross-origin to the API. Auth rides the bearer
  header (not cookies), so this is fine; just ensure the API's CORS allows the
  WebView origin if you keep the app on the bundled origin.
