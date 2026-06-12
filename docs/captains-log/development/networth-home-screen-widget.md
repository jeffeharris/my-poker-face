---
purpose: Narrative log of building the Net Worth iOS home-screen widget and the bugs hit along the way
type: guide
created: 2026-06-10
last_updated: 2026-06-10
---

# Net Worth home-screen widget — captain's log

## The goal
A real iOS home-screen widget showing the player's **net-worth sparkline** plus
their **renown, regard, and described status** (the reputation quadrant) — the
same trajectory the career lobby shows, but glanceable from the home screen.

## The shape of the problem
A widget can't run the web app — WidgetKit widgets are a separate SwiftUI
extension target with their own process. So the data has to be *handed* to them
through shared storage. The plan: the app writes a small JSON snapshot to an
**App Group** container; the widget reads it.

Three pieces: (1) a web publisher that packages the snapshot from the cash-lobby
data, (2) a tiny app-local Capacitor plugin (`WidgetBridge`) that writes the App
Group + reloads the widget, (3) the SwiftUI widget that reads and renders it.
All the data turned out to live in one call the app already makes —
`/api/cash/lobby` carries `bankroll_history`, `reputation.renown/regard`, and
`reputation.quadrant`.

The one upfront gate worth flagging: **App Groups require a paid Apple Developer
account.** Confirmed Jeff had one before building.

## The bug chain (and the wrong turns)

**1. CocoaPods vs. Xcode's "recommended settings."** Accepting Xcode's
"update to recommended settings" flipped on **User Script Sandboxing**, which
blocked CocoaPods' `[CP] Embed Pods Frameworks` script
(`Sandbox: ... deny file-read-data ... Pods-App-frameworks.sh`). Fix:
`ENABLE_USER_SCRIPT_SANDBOXING = NO`.

**2. The "no tables" scare — and the false attribution.** Right after placing the
widget, the app stopped loading tables. Easy to blame the new code — but the
backend told the truth: a flood of `database is locked`, including
`personality_for_seat lookup failed ... database is locked`. It was the deferred
SQLite write-storm: with `runware` image gen working, the **avatar-generation
burst** (image gen + usage/prompt-capture writes across many characters)
saturated SQLite's single writer and starved the lobby's own queries. Nothing to
do with the widget. Disabled on-demand avatar gen
(`ENABLE_AVATAR_GENERATION=false`) → storm gone, tables back. Lesson: when a new
feature "breaks" something, check the backend before blaming the feature.

**3. The widget showed "Open the app to sync" — write wasn't landing.** Read side
worked (it was reading an empty App Group); the app wasn't writing. Then a
debugging comedy of errors before the real one:
- My log-capture commands used `timeout` — **not a macOS command** — so every
  "capture" silently failed before `idevicesyslog` even started. (Install
  `coreutils` for `gtimeout`, or run the tool in the background and stop it.)
- `idevicesyslog` then captured only OS noise — **the app's Capacitor logs go to
  Xcode's console, not the device syslog**, when run from Xcode.
- Adding explicit logging to the publish path finally surfaced the truth in the
  Xcode console:
  `[widget] publishing snapshot {…}` → `[widget] publish FAILED: {"code":"UNIMPLEMENTED"}`.

**4. The root cause: Capacitor 6 doesn't auto-register app-local plugins.**
Reading `CapacitorBridge.swift` settled it: `registerPlugins()` only loads classes
listed in `capacitor.config.json`'s `packageClassList` — which is generated from
**npm plugin packages**. An app-local `CAPBridgedPlugin` is never in that list, so
the JS call returns `UNIMPLEMENTED`. The documented fix is explicit registration
via the `capacitorDidLoad()` hook: a `CAPBridgeViewController` subclass
(`MainViewController`) calling `bridge?.registerPluginInstance(WidgetBridgePlugin())`,
with `Main.storyboard` repointed to that subclass. (Tucked the subclass into the
existing plugin file to avoid another target-membership dance.)

## On the phone
After registration, the console read clean —
`[WidgetBridge] wrote snapshot … reloaded timelines` — and the widget flipped from
the placeholder to live data: net worth, the sparkline, "Disliked Nobody", and the
★/♥ badges.

## Loose ends
- The generated `NetWorthWidgetControl` (a sample Control Center widget) is left in
  the target; removable later.
- `ENABLE_AVATAR_GENERATION=false` is a stopgap — the real fix is the Postgres
  migration (no single-writer lock), then flip avatars back on.
- Production still must strip the dev-only `NSAllowsArbitraryLoads` and point at the
  https backend before any release. See `native-ios-on-device.md`.
