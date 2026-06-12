---
purpose: Technical reference + operations runbook for the native iOS app — architecture, how to build/run on device, point it at a backend, and ship to TestFlight
type: guide
created: 2026-06-11
last_updated: 2026-06-11
---

# iOS app — technical reference & runbook

The native iOS app is the **React SPA wrapped in Capacitor**. There's no separate
codebase — the same `react/react` app, built and embedded in a native shell. For
one-time scaffolding (Capacitor deps, Google OAuth clients, first `cap add ios`)
see [`IOS_NATIVE_SETUP.md`](./IOS_NATIVE_SETUP.md); this doc is the ongoing
build / run / release reference.

## Architecture at a glance

- **Wrapper:** Capacitor. iOS project lives at `react/react/ios/App`
  (`App.xcworkspace`). App id `com.mypokerface.app`; there's also a widget
  extension `com.mypokerface.app.NetWorthWidget` (the Net Worth home-screen
  widget) that ships embedded in the app and must be signed alongside it.
- **Embedded bundle, not live-reload.** `capacitor.config.ts` has `webDir: dist`
  and **no `server.url`** — the app runs the web bundle baked into the `.app` at
  build time. So **any JS/CSS change needs the 3-step chain**: `npm run build` →
  `npx cap copy ios` → Xcode/CLI build. Skipping the first two ships stale UI.
- **WebView origin is `capacitor://localhost`**, not the API host. All API calls
  are cross-origin to the backend.
- **Auth is bearer-token, not cookies.** The access token is attached as
  `Authorization: Bearer` to every API call (`src/utils/nativeAuth.ts`) and sent
  in the Socket.IO `auth` payload (`src/utils/socket.ts`, a *callback* so each
  reconnect reads the current token). Token storage is Capacitor Preferences;
  1h access / 30d refresh, auto-refreshed on 401.

## Pointing the app at a backend

The API/socket origin is **baked at build time** from `VITE_API_URL` /
`VITE_SOCKET_URL` (read in `src/config.ts`). Vite resolves them by **build mode**,
so the target is automatic — you don't pass env vars for the normal cases:

- **Production builds** (`npm run build`, `npm run ios`, `make testflight`) load
  `react/react/.env.production` → **https prod** (`https://mypokerfacegame.com`).
  Because that file is committed, a release/native bundle can never accidentally
  ship the cleartext dev URL, and **no ATS exception is needed**.
- **Dev server** (`npm run dev`, development mode) loads `react/react/.env` → the
  local Tailscale Mac backend (`http://macbook:5001`). `.env` is gitignored and
  dev-only; it does **not** affect production builds.

Vite precedence: shell env > `.env.production` (prod mode) / `.env` (dev mode). So
to point a build at a *different* backend, override on the command line:

```bash
cd react/react
VITE_API_URL=https://staging.example.com VITE_SOCKET_URL=https://staging.example.com npm run build
npx cap copy ios
```

To run the app against the **local Mac backend on a device**, build in
*development* mode (loads `.env`) and re-add the ATS exception (see gotchas):

```bash
npx vite build --mode development && npx cap copy ios
```

**Verify** after building: `grep -rl mypokerfacegame.com dist/assets/*.js` should
hit, and `grep -rl macbook:5001` should be empty.

Two backend-side requirements for a native client (both already in place):
- **CORS** must allow the WebView origins (`capacitor://localhost`, etc.) for
  REST *and* Socket.IO — `flask_app/extensions.py` (`_NATIVE_WEBVIEW_ORIGINS`),
  appended to the explicit prod allow-list.
- **CSRF** exempts `Authorization: Bearer` requests (`flask_app/csrf.py`) —
  bearer auth is CSRF-immune and can't read the `csrf_token` cookie cross-origin.
  Without this, every mutating call (actions, chat) 403s in prod.

## Build & run on a physical device (dev loop)

```bash
cd react/react
npm run build   # production mode → .env.production → https prod (see "Pointing the app at a backend")
npx cap copy ios
cd ios/App
# UDID: the *hardware* UDID, NOT the devicectl CoreDevice id —
#   xcodebuild -workspace App.xcworkspace -scheme App -showdestinations | grep 'platform:iOS,'
xcodebuild -workspace App.xcworkspace -scheme App -configuration Debug \
  -destination 'id=<HARDWARE_UDID>' -derivedDataPath ./build -allowProvisioningUpdates
xcrun devicectl device install app --device <CoreDevice-UUID> \
  ./build/Build/Products/Debug-iphoneos/App.app
xcrun devicectl device process launch --device <CoreDevice-UUID> com.mypokerface.app
```

- `devicectl list devices` gives the **CoreDevice UUID** (for install/launch);
  `xcodebuild -showdestinations` gives the **hardware UDID** (for `-destination`).
  They are different — using the wrong one is the classic "no device matched".
- **Launch fails if the phone is locked** (`FBSOpenApplicationErrorDomain
  error 7 / Locked`) — unlock and tap the icon, or re-run the launch.
- Dev builds sign with the **free Personal Team** → the app stops launching
  after **7 days**; just rebuild to refresh.

## Ship to TestFlight

One command (needs a **paid Apple Developer account**):

```bash
make testflight ASC_KEY_ID=<key id> ASC_ISSUER_ID=<issuer id>
```

It builds the prod-pointed web bundle → archives a Release build → exports an
App Store `.ipa` (via `react/react/ios/App/ExportOptions.plist`) → uploads via
`xcrun altool`. `BUILD_NUMBER` defaults to a timestamp so each upload gets a
unique, increasing `CFBundleVersion` (App Store Connect rejects duplicates).

**Prerequisites (one-time):**
1. **Paid Apple Developer Program** membership (the free Personal Team can't
   distribute). Xcode → Settings → Accounts signed into that team
   (`99MBTK6SLV`) so automatic signing can mint the Apple Distribution cert +
   App Store profiles for both the app and the widget.
2. **App Store Connect API key:** App Store Connect → Users and Access →
   Integrations → Keys → generate. Stage the `.p8` at
   `~/.appstoreconnect/private_keys/AuthKey_<KEY_ID>.p8` (where Apple's tools
   look). Note the **Key ID** and **Issuer ID** (the issuer is the UUID atop the
   Keys page) — those are the `make testflight` args. Key role ≥ App Manager.
3. **App record:** App Store Connect → Apps → ➕ New App → iOS, bundle id
   `com.mypokerface.app`, a name + SKU. The upload has nowhere to land without it.

**After upload:** the build *processes* for ~5–15 min (TestFlight → Builds), then
add **Internal Testers** by Apple ID — internal testing has **no Beta App Review**
and they install via the TestFlight app immediately.

Manual alternative to the `make` target: Xcode → Window → Organizer → select the
archive → Distribute App → App Store Connect → Upload (uses the signed-in account,
no API key).

## Native gotchas & guardrails

- **Xcode project `objectVersion` pinned to 56.** Xcode 16's "recommended
  settings" bumps it to 70, which the bundled CocoaPods `xcodeproj` can't read →
  `pod install` / `cap sync` fail with "object version 70". If it breaks again,
  reset `App.xcodeproj/project.pbxproj` to `objectVersion = 56`.
- **ATS / `NSAllowsArbitraryLoads` is removed** — production builds use https
  prod (`.env.production`), so default ATS (https/TLS-only) applies and no
  exception is needed. `ITSAppUsesNonExemptEncryption = false` is declared
  (standard TLS only) to skip the export-compliance prompt.
  ⚠️ A **dev-mode build pointed at the cleartext `http://macbook:5001` backend
  will fail silently** — the native Google flow succeeds (https to Google) but the
  follow-up `POST /api/auth/google/native` is blocked by ATS, surfacing as
  `Native Google login failed: {}`. To dev against the Mac on device, add a
  *scoped* exception to `Info.plist` (do **not** re-add blanket
  `NSAllowsArbitraryLoads` — it trips App Review):
  ```xml
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
  ```
  `NSAllowsLocalNetworking` permits cleartext only to local, dot-less hosts like
  `macbook`; it's release-safe but only needed for the dev loop.
- **Resume / reconnect:** recovery is driven by `@capacitor/app` `appStateChange`
  (WKWebView `visibilitychange` is unreliable); the socket reconnects with a
  fresh token; the hand-sequencer resets on resume so a frozen timer can't replay
  stale beats. See `src/hooks/usePokerGame.ts` / `useHandSequencer.ts`.
- **Haptics:** native-only, dynamically imported, no-op on web
  (`src/utils/haptics.ts`).

## Local Mac dev environment

Docker Desktop on Mac has two soft spots the dev setup works around, all in a
**gitignored `docker-compose.override.yml`** (so homehub/prod are untouched):
a named DB volume (VirtioFS bind mounts make SQLite ~30× slower), a
gunicorn+gevent server (the Werkzeug dev server + the Docker proxy drop the
realtime socket), and a groq Default LLM tier. Full story:
[`../captains-log/development/ios-stabilization-and-mac-docker.md`](../captains-log/development/ios-stabilization-and-mac-docker.md).

## Open / before a public release

- **Sign in with Apple** — Apple requires it on the App Store and for **external**
  TestFlight whenever you offer third-party login (Google). Internal testing
  doesn't need it. Backend pattern mirrors `/api/auth/google/native`.
- **Android** — the auth/transport layer is platform-agnostic; only the OAuth
  client + `cap add android` remain.
