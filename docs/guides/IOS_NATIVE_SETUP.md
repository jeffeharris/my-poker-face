---
purpose: Take the existing React app to a native iOS/Android build (Capacitor) with native Google sign-in against the bearer-token auth backend
type: guide
created: 2026-06-08
last_updated: 2026-06-11
---

# Native (iOS / Android) Setup

> **One-time scaffolding only.** For the ongoing build / run-on-device / point-at-a-backend /
> TestFlight-release workflow and native gotchas, see [`IOS_APP.md`](./IOS_APP.md).

The app is wired for a native build end-to-end **in code** — backend auth,
frontend transport, Capacitor config, secure token storage, and the native
Google sign-in button are all committed. What remains are the steps that need a
Mac (Xcode), the Google Cloud Console, and on-device run.

## What's already in place

| Layer | Status |
|---|---|
| `POST /api/auth/google/native` — verify Google ID token → JWT pair | ✅ `poker/auth.py` |
| `POST /api/auth/token/refresh` — rotating refresh → access token | ✅ `poker/auth.py` |
| Bearer accepted by `get_current_user` + Socket.IO `authenticate_socket` | ✅ |
| Multi-platform audience allowlist (`GOOGLE_ALLOWED_AUDIENCES`) | ✅ `flask_app/config.py` |
| Bearer injection on every API call + 401 refresh-retry | ✅ `src/utils/csrf.ts`, `src/utils/nativeAuth.ts` |
| Socket.IO `auth` payload on native | ✅ `src/utils/socket.ts` |
| `useAuth.loginWithGoogleNative()` + token load/clear | ✅ `src/hooks/useAuth.tsx` |
| Capacitor deps + `capacitor.config.ts` (appId `com.mypokerface.app`) | ✅ `react/react/` |
| Secure token storage (Preferences) + Google init bootstrap | ✅ `src/native/bootstrap.ts` |
| Native Google sign-in wired into the login button | ✅ `src/components/auth/LoginForm.tsx` |

All native code is dynamically imported and gated on `isNativePlatform()`, so the
web build is unchanged (verified: `tsc`, `vitest`, `vite build` all green).

## Remaining steps (Mac)

### 1. OAuth clients (Google Cloud Console)

- **iOS client** — already created (Application type "iOS", bundle id
  `com.mypokerface.app`). ✅
- **Android** — the Capacitor Android project now exists
  (`react/react/android`, see [`ANDROID_APP.md`](./ANDROID_APP.md)). To make
  Google sign-in work you still need an Android OAuth client (Application type
  "Android", package `com.mypokerface.app` + debug/release SHA-1). Not needed for
  the iOS build.

Set the **backend** env vars (your `.env` + prod env) so the token audience is
accepted:
```bash
GOOGLE_IOS_CLIENT_ID=<your iOS client>.apps.googleusercontent.com
# GOOGLE_ANDROID_CLIENT_ID=...   # when you add Android
```
(`GOOGLE_CLIENT_ID`, the existing web client, is already in the allowlist and is
the `aud` on Android.)

### 2. Frontend build-time env (`react/react/.env` or shell)

The native WebView's origin is `capacitor://localhost`, **not** the API — so pin
the API origin and the Google client at build time:
```bash
VITE_API_URL=https://mypokerfacegame.com
VITE_SOCKET_URL=https://mypokerfacegame.com
VITE_GOOGLE_CLIENT_ID=<your web/server client>.apps.googleusercontent.com
```

### 3. Generate the iOS project

From `react/react/`:
```bash
npm install            # picks up the Capacitor deps already in package.json
npm run build          # produce dist/ (with the env vars from step 2 set)
npx cap add ios
npx cap sync
```

### 4. iOS native config (Xcode / Info.plist)

The Google sign-in plugin needs two Info.plist entries (in `ios/App/App/Info.plist`):

1. **`GIDClientID`** = your **iOS** client ID:
   ```xml
   <key>GIDClientID</key>
   <string>YOUR_IOS_CLIENT_ID.apps.googleusercontent.com</string>
   ```
2. **URL scheme** = the *reversed* iOS client ID, so Google can redirect back:
   ```xml
   <key>CFBundleURLTypes</key>
   <array>
     <dict>
       <key>CFBundleURLSchemes</key>
       <array>
         <string>com.googleusercontent.apps.YOUR_IOS_CLIENT_ID</string>
       </array>
     </dict>
   </array>
   ```
   (Use the `com.googleusercontent.apps.NNN-xxx` form — it's the client ID with
   the two dot-segments reversed.)

Confirm the Xcode target's **Bundle Identifier** is `com.mypokerface.app`.

### 5. Run

```bash
npm run ios     # build + cap sync ios + cap open ios → run from Xcode
```

Sign in with Google on a simulator/device → the plugin returns an ID token →
`loginWithGoogleNative` posts it to `/api/auth/google/native` → tokens are stored
in Preferences and every API/socket call is authenticated automatically.

### 6. Android (already scaffolded)

The Android equivalent of steps 3–5 is already done and committed at
`react/react/android` (generated with `npm i @capacitor/android && npx cap add
android`). The native config that differs from iOS — the Google
`server_client_id` string resource, the Android OAuth client + SHA-1, the signing
keystore, and the Play Store release — is documented in
[`ANDROID_APP.md`](./ANDROID_APP.md). Build it with `make android-debug` (needs a
JDK 17+ and the Android SDK).

## Troubleshooting

- **Sign-in fails before hitting the backend** (no `Native Google ID token
  rejected` log on the server): it's a client-side Google config issue —
  Info.plist `GIDClientID` / URL scheme / bundle id mismatch.
- **401 from `/api/auth/google/native`** with a server log
  `Native Google ID token rejected: ...`: an `aud`/issuer mismatch. Decode the
  ID token (paste into jwt.io or log `user.authentication.idToken`) and confirm
  its `aud` is in `GOOGLE_ALLOWED_AUDIENCES` (`GOOGLE_CLIENT_ID` /
  `GOOGLE_IOS_CLIENT_ID`).
- **API/socket calls fail cross-origin**: ensure the API's `CORS_ORIGINS`
  allows the WebView origin (`capacitor://localhost`). Auth is bearer (not
  cookies), so credentialed CORS isn't required.

## Notes

- **Sign in with Apple**: iOS App Store review requires it when you offer Google
  sign-in. The backend pattern is identical — add an Apple-token-verifying
  endpoint mirroring `/api/auth/google/native` plus a `loginWithApple()` hook.
  Not required to run the app.
- **Token lifetimes**: 1h access / 30d refresh (`poker/auth.py`). The frontend
  refreshes on 401 automatically (single-flight). Refresh is stateless/rotated;
  add server-side tracking if you need revocation.
- **Stronger at-rest storage**: swap `@capacitor/preferences` in
  `src/native/bootstrap.ts` for a Keychain/Keystore plugin behind the same
  `TokenStorage` interface — `configureTokenStorage` is the only touch-point.
