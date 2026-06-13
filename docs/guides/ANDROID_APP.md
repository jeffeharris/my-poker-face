---
purpose: Technical reference + operations runbook for the native Android app — architecture, how to build/run on device, point it at a backend, sign, and ship to the Play Store
type: guide
created: 2026-06-11
last_updated: 2026-06-11
---

# Android app — technical reference & runbook

The native Android app is the **same React SPA wrapped in Capacitor** as the iOS
app — there's no separate codebase. It's the `react/react` app, built and embedded
in a native shell at `react/react/android`. This is the Android sibling of
[`IOS_APP.md`](./IOS_APP.md); the one-time scaffolding story (Capacitor deps,
Google OAuth, `cap add`) lives in [`IOS_NATIVE_SETUP.md`](./IOS_NATIVE_SETUP.md).

> **Build host:** Android needs a **JDK 17+ and the Android SDK** (both come with
> Android Studio) the way iOS needs a Mac + Xcode. You can scaffold/sync the
> project anywhere Node runs, but compiling an APK/AAB needs the SDK.

## Architecture at a glance

- **Wrapper:** Capacitor. Android project lives at `react/react/android`
  (a standard Gradle project — open the `android/` folder in Android Studio). App
  id `com.mypokerface.app` (both `namespace` and `applicationId` in
  `app/build.gradle`). It ships the **Net Worth home-screen widget** (the Android
  counterpart to the iOS NetWorthWidget — see [Home-screen widget](#home-screen-widget)).
- **Embedded bundle, not live-reload.** `capacitor.config.ts` has `webDir: dist`
  and **no `server.url`** — the app runs the web bundle baked into the APK at
  build time. So **any JS/CSS change needs the 3-step chain**: `npm run build` →
  `npx cap copy android` → Gradle build. Skipping the first two ships stale UI.
- **WebView origin is `https://localhost`** (Capacitor's Android scheme), not the
  API host — every API call is cross-origin to the backend, same as iOS.
- **Auth is bearer-token, not cookies.** Identical to iOS: the access token is
  attached as `Authorization: Bearer` to every API call
  (`src/utils/nativeAuth.ts`) and sent in the Socket.IO `auth` payload
  (`src/utils/socket.ts`). Token storage is Capacitor Preferences; 1h access /
  30d refresh, auto-refreshed on 401. None of this is platform-specific — it's all
  gated on `isNativePlatform()` and already runs on iOS.

## Pointing the app at a backend

Identical model to iOS — the API/socket origin is **baked at build time** from
`VITE_API_URL` / `VITE_SOCKET_URL` (read in `src/config.ts`), resolved by Vite
**build mode**:

- **Production builds** (`npm run build`, `npm run android`, `make android-debug`,
  `make android-release`) load `react/react/.env.production` → **https prod**
  (`https://mypokerfacegame.com`). Committed, so a release bundle can't ship the
  dev URL.
- **Dev server** (`npm run dev`) loads `react/react/.env` → the local Mac backend.

Override the target on the command line just like iOS:

```bash
cd react/react
VITE_API_URL=https://staging.example.com VITE_SOCKET_URL=https://staging.example.com npm run build
npx cap copy android
```

**Verify** after building: `grep -rl mypokerfacegame.com dist/assets/*.js` should
hit and `grep -rl macbook:5001` should be empty.

Two backend-side requirements (both already in place, shared with iOS):
- **CORS** must allow the WebView origins (`https://localhost`,
  `capacitor://localhost`) for REST *and* Socket.IO — `flask_app/extensions.py`
  (`_NATIVE_WEBVIEW_ORIGINS`).
- **CSRF** exempts `Authorization: Bearer` requests (`flask_app/csrf.py`).

## Google Sign-In setup (the one real Android-specific step)

The auth backend already allowlists an Android audience
(`GOOGLE_ANDROID_CLIENT_ID` → `GOOGLE_ALLOWED_AUDIENCES` in `flask_app/config.py`),
and the frontend flow is platform-agnostic. Two things differ from iOS:

1. **The plugin reads a different config slot on Android.** Unlike iOS (which
   reads `iosClientId` / `serverClientId` from `capacitor.config.ts`), the
   `@codetrix-studio/capacitor-google-auth` Android side resolves the client id
   as `androidClientId` → `clientId` → the **`server_client_id` string
   resource**. We set it in
   `android/app/src/main/res/values/strings.xml` to the **web/server** client id
   (the same value as `capacitor.config.ts` `GoogleAuth.serverClientId`). That
   makes `requestIdToken` mint an ID token whose `aud` is the web client, which
   the backend already accepts as `GOOGLE_CLIENT_ID`. Keep the two in sync.
2. **You must register an Android-type OAuth client in Google Cloud Console** —
   package `com.mypokerface.app` plus the build's **SHA-1** certificate
   fingerprint. Without it Google rejects sign-in with **status 10
   (`DEVELOPER_ERROR`)** before any request reaches the backend. Get the SHA-1:
   ```bash
   # Debug builds (the auto debug keystore):
   keytool -list -v -alias androiddebugkey -keystore ~/.android/debug.keystore \
     -storepass android -keypass android | grep SHA1
   # Release builds — from your upload keystore:
   keytool -list -v -alias <your-alias> -keystore <your-keystore>.jks | grep SHA1
   ```
   Add **both** the debug and release/Play-App-Signing SHA-1s as separate Android
   clients (or one client with multiple fingerprints). Optionally set
   `GOOGLE_ANDROID_CLIENT_ID` in the backend `.env` if you want the Android client
   id explicitly allowlisted (not required — the web client id is the token `aud`).

## Build & run on a physical device (dev loop)

Enable USB debugging on the device, then:

```bash
make android-debug          # prod-pointed web build → cap copy → gradlew assembleDebug
adb install -r react/react/android/app/build/outputs/apk/debug/app-debug.apk
adb shell monkey -p com.mypokerface.app 1   # or just tap the icon
```

Or open `react/react/android` in **Android Studio** and Run ▶. Either way, run
`npm run build && npx cap copy android` first (or use the `make` target) so the
embedded bundle is current. `npm run android` does the build + sync + opens
Android Studio in one step.

### Dev against the local Mac backend (cleartext)

Production points at https, so default Android network security applies and no
exception is needed. But a **dev build pointed at the cleartext
`http://macbook:5001`** is blocked by Android's default
`cleartextTrafficPermitted=false` (targetSdk ≥ 28) — sign-in to Google succeeds
(https) but the follow-up `POST /api/auth/google/native` fails silently, the exact
mirror of the iOS ATS gotcha. To dev against the Mac, add a **scoped, debug-only**
network security config (do **not** enable blanket cleartext — it weakens release
and trips Play review):

```xml
<!-- react/react/android/app/src/debug/res/xml/network_security_config.xml -->
<network-security-config>
  <domain-config cleartextTrafficPermitted="true">
    <domain includeSubdomains="true">macbook</domain>
    <domain includeSubdomains="true">10.0.2.2</domain> <!-- emulator → host loopback -->
  </domain-config>
</network-security-config>
```

```xml
<!-- react/react/android/app/src/debug/AndroidManifest.xml (debug overlay) -->
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <application android:networkSecurityConfig="@xml/network_security_config" />
</manifest>
```

Placing these under `src/debug/` means they apply only to debug builds; the
release AAB stays https-only. (This is intentionally documented rather than
committed — matches how iOS leaves `NSAllowsLocalNetworking` as a manual dev step.)

## Ship to the Play Store

```bash
make android-release ASC_unused...   # builds a signed AAB; needs android/key.properties
```

Unlike Apple's automatic signing, Android needs an **explicit upload keystore**.
One-time setup:

1. **Generate an upload keystore** (keep it safe — losing it means a painful key
   reset with Google):
   ```bash
   keytool -genkey -v -keystore upload-keystore.jks -keyalg RSA -keysize 2048 \
     -validity 10000 -alias upload
   ```
2. **Create `react/react/android/key.properties`** (gitignored — never commit it):
   ```properties
   storeFile=/absolute/path/to/upload-keystore.jks
   storePassword=********
   keyAlias=upload
   keyPassword=********
   ```
   `app/build.gradle` reads this and applies the release `signingConfig` only when
   the file exists, so debug builds and fresh checkouts need nothing.
3. **Create the app in the Play Console** (com.mypokerface.app), and enable
   **Play App Signing** (recommended). Register the **App Signing** SHA-1 it gives
   you as a Google OAuth Android client too (step 2 of Google Sign-In above) — the
   installed app is signed with Google's key, so that's the fingerprint that
   reaches Google at runtime.

Then:

```bash
make android-release            # → react/react/android/app/build/outputs/bundle/release/app-release.aab
```

`BUILD_NUMBER` (a timestamp by default) is passed as both `versionCode` and
`versionName`, so each upload gets a unique, increasing `versionCode` (Play
rejects duplicates) — the Android analogue of the iOS `CFBundleVersion` bump.
Upload the `.aab` to an **Internal testing** track for the fastest tester loop
(no full review; testers install from a Play link in minutes).

## Home-screen widget

The **Net Worth widget** mirrors the iOS NetWorthWidget: net-worth headline, a
green/red trend sparkline, the player's status, and ★ renown / ♥ regard. Same data,
same web producer — `src/utils/widgetData.ts` publishes one `WidgetSnapshot` JSON
on both platforms; no platform branches in the web app.

Pieces (all in `react/react/android/app/src/main`):

| File | Role |
|---|---|
| `java/com/mypokerface/app/WidgetBridgePlugin.java` | Capacitor plugin `WidgetBridge.publish({payload})` — writes the snapshot JSON to `SharedPreferences` and pokes the widget. |
| `java/com/mypokerface/app/NetWorthWidgetProvider.java` | `AppWidgetProvider` — reads the snapshot, draws the sparkline to a `Bitmap`, fills the `RemoteViews`. |
| `java/com/mypokerface/app/MainActivity.java` | Registers `WidgetBridgePlugin` in `onCreate` (app-local plugins aren't auto-registered). |
| `res/layout/widget_net_worth.xml`, `res/drawable/widget_background.xml` | RemoteViews layout + dark rounded card. |
| `res/xml/net_worth_widget_info.xml` + the `<receiver>` in `AndroidManifest.xml` | Widget sizing/metadata + registration. |

**Key difference from iOS:** no App Group. The iOS widget is a separate process,
so it needs a shared App Group container; the Android widget is a `receiver` in the
**same app package**, so it reads the app's own `SharedPreferences` directly — and
there's no separate widget target to sign. Live updates work the same way: the app
pushes on each lobby refresh (`WidgetBridge.publish` → `AppWidgetManager` update),
with a ~30 min framework fallback cadence (`updatePeriodMillis`).

To tweak the look, edit the layout/colors and `NetWorthWidgetProvider.drawSparkline`;
these are plain Android resources/code, unaffected by `cap copy`/`cap sync`.

## On-device chat suggestions (Gemini Nano)

Quick-chat suggestions can be generated **on-device** via Gemini Nano (ML Kit GenAI
**Prompt API**) — the Android counterpart to the iOS Foundation Models path. Same bridge
contract: `OnDeviceLLMPlugin.kt` registers under the `FoundationModels` jsName, so the
shared `src/utils/onDeviceLLM.ts` + the `api.ts` server-composes-parity routing drive iOS
and Android identically, with the server route as transparent fallback.

- **Dep + floor:** `com.google.mlkit:genai-prompt` requires **minSdk 26** and Kotlin (the
  artifact ships Kotlin 2.2 metadata → the app module applies the Kotlin 2.2 Gradle plugin).
- **Where it runs:** real generation needs **AICore + Gemini Nano** (Pixel 9/10-class). On
  anything else (incl. the standard emulator) `availability()` reports false and suggestions
  come from the server — verified: the plugin registers and the app runs clean on a
  non-AICore emulator.
- **Design rationale + the full CallType analysis:** `docs/technical/ON_DEVICE_LLM_FEASIBILITY.md`.

## Native gotchas & guardrails

- **JDK / SDK required to build.** `assembleDebug` / `bundleRelease` need a JDK 17+
  and the Android SDK; set `JAVA_HOME` and let Gradle find the SDK via
  `ANDROID_HOME` or `android/local.properties` (`sdk.dir=...`, gitignored). The
  Capacitor *scaffold* (`cap add/copy/sync android`) runs without them.
- **Toolchain bumped off Capacitor's pins.** Capacitor 6 ships AGP 8.2.1 / Gradle
  8.2.1, which predate JDK 21 and so won't sync under a current Android Studio's
  bundled JBR 21. We bumped them to **AGP 8.7.3 + Gradle 8.9** (`android/build.gradle`,
  `gradle/wrapper/gradle-wrapper.properties`) so the build runs natively on JDK 21 —
  no second JDK needed. `compileSdk`/`targetSdk` stay at **34**, so install **SDK
  Platform 34** (Android Studio → SDK Manager, or it prompts on first sync). If a
  future `cap` upgrade regenerates these, re-apply the bump.
- **Building from Windows against a WSL-hosted checkout:** the Gradle *wrapper
  script* can't run over a `\\wsl.localhost\...` UNC path (`cmd` rejects a UNC cwd),
  but Android Studio drives Gradle via its Tooling API and handles the UNC project
  path. So build from the IDE (or move the checkout onto the Windows filesystem / put
  the SDK in WSL) rather than calling `gradlew.bat` from a Windows shell.
- **`colors.xml` is hand-added.** Capacitor 6's Android template references
  `@color/colorPrimary` etc. from `styles.xml` but doesn't ship the file; we add
  `android/app/src/main/res/values/colors.xml` (dark `#0a0b10` chrome + `#dc2626`
  accent). If a future `cap` upgrade regenerates the template, keep that file.
- **Icons & splash** are generated from the iOS art via `@capacitor/assets`
  (`npm run assets:generate` in `react/react`, source images in `react/react/assets/`).
  Re-run it after changing the brand art.
- **Edge-to-edge (targetSdk 35).** If you bump `targetSdkVersion` to 35+, Android
  enforces edge-to-edge and the WebView can slide under the status/navigation bars
  — the same class of inset problem the iOS shell solves with
  `contentInset: 'always'`. Handle it with `@capacitor/status-bar` +
  `env(safe-area-inset-*)` CSS, or stay on targetSdk 34 until you do a safe-area
  pass. Current target is 34.
- **Resume / reconnect, Haptics, Keyboard** all work unchanged — they're driven by
  `@capacitor/app`, `@capacitor/haptics`, `@capacitor/keyboard`, which are
  cross-platform. The accessory-bar hide in `src/native/bootstrap.ts` is an iOS
  no-op-on-Android.

## Deferred / parity gaps

- **Sign in with Apple** — not required on Android (it's an Apple App Store rule).
- **Push notifications** — neither platform ships them yet; on Android that means
  Firebase + `google-services.json` (the `app/build.gradle` already has the
  conditional `google-services` apply block ready for it).
