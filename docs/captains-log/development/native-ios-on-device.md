---
purpose: Narrative log of wrapping the React app as a native iOS app and getting it running, signed, on a physical iPhone
type: guide
created: 2026-06-09
last_updated: 2026-06-09
---

# Native iOS on device — captain's log

## The goal
Ship the existing React frontend as a real iOS app — not a plan, a running app on
an actual iPhone, signed in with Google, playing against the AI. The backend half
(native Google sign-in endpoint, refresh tokens, Socket.IO bearer auth, the
frontend bearer/transport layer + Capacitor scaffold) was already built and tested
in the cloud. This session was the other half: stand up the Mac toolchain, build,
and chase down everything that only breaks on a real device.

## Toolchain (the unglamorous part)
Started from a Mac with Xcode downloaded but never moved into `/Applications` —
so `xcode-select` was pointed at the Command Line Tools and `xcodebuild` refused
to run. Moved Xcode in, repointed `xcode-select`, accepted the license, installed
CocoaPods via Homebrew (the `brew link` "failure" was a benign `xcodeproj`
shadow — the formula installed fine). `cap add ios` then `pod install` succeeded,
and the app built + launched in the iOS 26 simulator showing the landing screen.

## The bug chain (four wrong turns, each a good lesson)

**1. Guest login hung — and the logs lied.** First instinct was App Transport
Security blocking cleartext `http://localhost`. Added the ATS exception... still
hung. The backend logs showed *nothing* arriving, which screamed "request never
left the phone." **False signal:** the dev backend doesn't log requests at all —
a direct `curl` succeeded and *also* didn't appear in the logs. The real tell was
in the curl response: no `Access-Control-Allow-Origin` for `capacitor://localhost`.
The WebView's origin is `capacitor://localhost`, and CORS didn't allow it, so the
browser silently dropped every credentialed response. Added the Capacitor origins
to the dev CORS allow-list (REST) → guest login worked. Lesson: don't trust
"nothing in the logs" until you've proven the logs would show it.

**2. "Can't start a game" — Socket.IO has its own CORS.** Same root cause, second
door: `_get_socketio_cors_origins()` is a *separate* allow-list from the REST CORS.
The game needs a live socket; its handshake was rejected. Added the same origins
there.

**3. Google sign-in crashed the app (SIGSEGV).** The crash report pointed straight
at `-[GIDSignIn signInWithOptions:]` raising — Google's SDK aborting because it had
no client ID. I'd put `GIDClientID` in Info.plist, but reading the plugin's Swift
source showed it never looks there: `@codetrix-studio/capacitor-google-auth` reads
`iosClientId`/`clientId` from the Capacitor config. Added the GoogleAuth plugin
block to `capacitor.config.ts`.

**4. Then `401 invalid_client`.** Progress — now talking to Google, but rejected.
The log showed a stale placeholder client_id at first (red herring from an old
build), but the real bug was ours: `googleSignIn.ts` called
`GoogleAuth.initialize({ clientId: VITE_GOOGLE_CLIENT_ID })` — the **web** client —
and the plugin's `call.getString("clientId") ?? <iosClientId>` meant the JS arg
*overrode* the iOS client. Google rejects a web client in the native flow. Dropped
the `clientId` from `initialize()` so it falls back to the config's `iosClientId`.
Sign-in worked. (Passkey only completes on a physical device, not the simulator —
which became the nudge to go to real hardware.)

## The cash-mode detour (a stale image, not a bug)
Sitting at a cash table threw `ValueError: Groq API key not provided`. The
traceback's line numbers didn't match the local source — because `docker-compose`
mounts `poker/`, `flask_app/`, `core/` as live volumes but **not `cash_mode/`**,
which was frozen in the baked image with older bot code. Mounted `cash_mode/` live;
the live code's `PlayerPsychology → EmotionalStateGenerator → StructuredLLMCategorizer`
still builds a Nano-tier client (Groq), which had no key. Jeff added a Groq key (and
a full set of provider keys); with `env_file` already loading `.env`, a recreate
picked it up and the world-ticker ran clean. Lesson: when a traceback's lines don't
match your source, suspect a stale artifact before suspecting the logic.

## The `database is locked` we chose not to fix
Under the first-load burst — AI avatar generation (now that `runware` works) + the
cash ticker + per-LLM-call usage tracking + the sit request all writing at once —
SQLite's single writer throws `OperationalError`, then succeeds on retry once the
burst clears. Confirmed avatars are cached **globally** per `(personality, emotion)`,
so it's a one-time system-wide warm-up, not a per-user tax. With Postgres on the
roadmap (MVCC fixes this properly) and a single low-concurrency environment, we
deliberately *skipped* tuning the soon-to-be-replaced SQLite. (Noted that
`busy_timeout` is a max wait, not a fixed stall — so the worry about "30s stalls"
doesn't apply; we skipped anyway on principle.)

## On the phone
Real device added two things the simulator gave for free: code signing (Jeff's
Apple ID / free Personal Team — the app runs 7 days then re-Run) and real
networking (`localhost` is the phone now, not the Mac). Pointed the app at the
Mac's backend over **Tailscale** (`http://macbook:5001`, MagicDNS) so it works on
any network; added `macbook` + `.ts.net` to Vite's `allowedHosts` and a dev-only
`NSAllowsArbitraryLoads` for the cleartext dev backend. Two device gotchas: Xcode's
"recommended settings" flipped on User Script Sandboxing which blocks CocoaPods'
embed script (`ENABLE_USER_SCRIPT_SANDBOXING = NO`), and the dev cert must be
trusted on the phone (Settings → General → VPN & Device Management).

Result: **My Poker Face running natively on an iPhone, signed in with Google via
passkey, playing against AI on the Mac's backend over Tailscale.**

## The one production landmine
`Info.plist` ships `NSAllowsArbitraryLoads` (dev-only, for the cleartext dev
backend). It MUST be removed — or ATS scoped to the prod `https` origin — before
any TestFlight/App Store build, alongside pointing the app at the deployed `https`
backend and adding `capacitor://localhost` to the prod `CORS_ORIGINS`. App Store
will also want Sign in with Apple (required when offering Google) and a paid
developer account.
