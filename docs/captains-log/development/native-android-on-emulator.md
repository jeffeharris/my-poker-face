---
purpose: Narrative log of wrapping the React app as a native Android app (Capacitor), the Net Worth widget, and getting it running on an emulator — plus the WSL/Windows toolchain saga
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# Native Android on emulator — captain's log

## The goal
Mirror the iOS app for Android: the same React SPA wrapped in Capacitor, the Net
Worth home-screen widget, running on a device. The whole bearer-token
auth/transport layer was already cross-platform and gated on `isNativePlatform()`,
so "in a similar fashion" was mostly scaffolding + the Android-specific native
bits + actually building the thing. See [`native-ios-on-device.md`](./native-ios-on-device.md)
for the sibling story.

## The build half (the easy part, in the cloud)
- `npm i @capacitor/android` + `npx cap add android` → `react/react/android`,
  committed like `ios/`. Five plugins (incl. google-auth) auto-registered.
- Three gaps the generated 6.x template shipped with:
  - **Missing `colors.xml`** — `styles.xml` references `@color/colorPrimary` etc.
    but the file isn't generated, so the build can't compile. Hand-added (dark
    `#0a0b10` chrome + `#dc2626` accent).
  - **Google Sign-In reads a different slot on Android** — the @codetrix-studio
    plugin resolves `androidClientId → clientId → R.string.server_client_id`, NOT
    the `serverClientId` key iOS reads from `capacitor.config.ts`. Added
    `server_client_id` (the **web** client) to `strings.xml`. Echo of iOS
    wrong-turn #3: same plugin, different platform, different config path.
  - **Icons/splash** generated from the existing iOS art via `@capacitor/assets`.
- **Net Worth widget:** Android has **no App Group**, so unlike iOS's
  shared-container dance, the `WidgetBridge` plugin just writes the snapshot to the
  app's own `SharedPreferences` and an `AppWidgetProvider` reads it back. Sparkline
  drawn to a `Bitmap` (RemoteViews can't host a custom view). Registered in
  `MainActivity.onCreate` (app-local plugins aren't auto-registered).

## The toolchain fight (the actual hard part)
This repo lives in WSL; the dev machine is Windows. Every tool that crossed that
boundary fought back:
- **Windows git/npm over the `\\wsl.localhost` UNC path**: npm choked on
  `C:\Windows\package.json`; Git-Bash rewrote `/home/...` args into
  `C:\Program Files\Git\home\...` (fixed with `MSYS_NO_PATHCONV=1`); WSL's node
  lives under nvm, off PATH in non-interactive shells.
- **`core.fileMode`**: Windows-side git can't see the Unix exec bit, so every
  `.sh`/`.py`/`gradlew` showed a phantom `100755→100644` diff. Set
  `core.fileMode false`; preserved gradlew's bit with `git add --chmod=+x`.

## Getting it running (the four wrong turns, Android edition)

**1. Opened the repo root in Android Studio → ▶ ran Docker.** The root has
`docker-compose.yml`, so the IDE's only run config was "Docker Dev." A Capacitor
Android project must be opened at the **platform folder** (`react/react/android`),
the way Xcode opens `ios/App` — not the repo.

**2. "Restricted write permissions" on the WSL drive.** Android Studio refuses to
fully import a project over `\\wsl.localhost` — it can't verify writability through
the 9P reparse points. The files were 100% writable (proven from WSL: `touch`
succeeded); the warning is a JetBrains-on-WSL limitation, not a real permission
problem. The fix that actually stuck: a native Windows-side checkout
(`git clone` into `StudioProjects`).

**3. The fresh-clone trifecta.** A cloned Capacitor project is missing everything
gitignored, and Gradle dies on each in turn:
   - **`node_modules`** — the build references `../node_modules/@capacitor/...`;
     `npm install`.
   - **`os=linux` in the user's global `~/.npmrc`** — forced npm to install
     **Linux** rollup/esbuild binaries on Windows, so `vite build` couldn't run
     (`Could not load the rollup native module`). Overrode with
     `npm install --os=win32 --cpu=x64`; later removed the line (it'll bite any
     Windows clone).
   - **Generated Capacitor files** — `cordova.variables.gradle` and the web bundle
     in `app/src/main/assets/public` aren't in git; Gradle's
     "Could not read script ...cordova.variables.gradle" is the tell. Regenerated
     with `npm run build && npx cap sync android`.
   - *Lesson: a committed Capacitor native project is not buildable from a bare
     clone — it needs `npm install` + `cap sync` first, exactly as iOS needs
     `pod install`. The canonical entry point is `npm run android`, not opening the
     folder cold.*

**4. SDK Platform 34 missing.** A fresh Android Studio shipped only `android-36.1`;
the project pins compileSdk 34, so sync/build can't find the platform. Installed
`platforms;android-34` + `build-tools;34.0.0` via the (downloaded) cmdline-tools
`sdkmanager`. Also bumped the project off Capacitor's pinned AGP 8.2.1 / Gradle
8.2.1 → **AGP 8.7.3 / Gradle 8.9** so it runs on Android Studio's bundled
**JDK 21** (the old pins predate JDK 21 and won't sync under it).

Then: `gradlew assembleDebug` → **BUILD SUCCESSFUL**, a 16.6 MB `app-debug.apk` —
first clean compile, widget and google-auth plugin and all. The app code was never
the problem; the environment was.

## The emulator (the final boss)
No physical device, so it was all command line: `sdkmanager` the
`system-images;android-34;google_apis;x86_64` image (~1.5 GB), `avdmanager create
avd -d pixel_7`, launch, `adb wait-for-device` until `getprop sys.boot_completed`,
`adb install -r`, `am start`. The first screencap caught the launcher (timing), but
logcat showed `Capacitor: Handling local request: https://localhost/ → index.js/css`
— it *was* loading. Fresh shot: the **My Poker Face login screen**, on a virtual
Pixel, talking to prod. Set a device PIN (`adb shell locksettings set-pin 1234`) so
passkeys have a secure lock screen. Confirmed `VIBRATE` is granted — haptics ride
the shared cross-platform `@capacitor/haptics`, though an emulator has no motor to
feel them.

*Lesson: the emulator lies — flat sRGB (no P3/HDR), choppy WebView animation, no
haptics. Don't judge color, motion, or feel on it; those are real-hardware calls.*

## What's left (external, mirrors iOS)
- **Android OAuth client** registered in Google Cloud (type Android, package
  `com.mypokerface.app` + the **debug** keystore SHA-1) so Google Sign-In works in
  dev; a release/Play-App-Signing SHA-1 later. No JSON download, no
  `google-services.json` — that's Firebase, which we don't use.
- Release keystore + `key.properties` + Play Store (the `make android-release` AAB
  path). Sign-in-with-Apple and the iOS-only widget extras stay iOS-only.

## A thread for later
Pixel 9/10 expose **Gemini Nano on-device** via AICore + the ML Kit **GenAI Prompt
API**. It maps cleanly onto the cheap `Fast`/`Nano` LLM tiers (chat suggestions,
lobby narration, beat cleanup) — $0 / offline / private, behind a native Capacitor
plugin (same `WidgetBridge` bridging pattern) with a cloud fallback for devices
without it. iOS's parallel is Apple's Foundation Models framework. Noted, not built.

Result: **My Poker Face running natively on an Android emulator, login screen
rendering against prod, the Net Worth widget compiled in, haptics wired — first
clean build the moment the environment stopped fighting.**
