---
purpose: Narrative log of stabilizing the native iOS app on device — game-state desync fixes, an expanded haptic vocabulary, and the Mac/Docker-Desktop performance + socket gauntlet
type: guide
created: 2026-06-10
last_updated: 2026-06-10
---

# iOS device stabilization + the Mac/Docker gauntlet — captain's log

## The goal
The native app already ran on a physical iPhone (see `native-ios-on-device.md`).
This session was about making it *feel* and *behave* right: native haptics worth
having, the mid-hand desyncs that don't happen on the web, and — the rabbit hole
that ate the back half of the day — a cluster of problems that turned out to be
**Docker Desktop for Mac**, not the code.

## Haptics: from a subtle tap to a vocabulary
Started by expanding the native haptics so you can follow the action eyes-free:
your turn, each board card, opponent raise/all-in, win/loss, and a **looping
heartbeat through an all-in showdown** (started at the hole-card reveal *only if
you're in the pot*, stopped when the winner lands). Built a tiny sequencing
engine (`hapticSequence`) because iOS exposes only discrete taps — distinctive
"feels" come from sequencing taps at millisecond offsets.

First pass felt too subtle. The reason: everything used `Haptics.impact`
(light/medium/heavy), which Apple deliberately tunes refined — and there's no
intensity parameter. The punch came from the two stronger mechanisms we weren't
using: `Haptics.notification` (the OS success/error buzzes) and `Haptics.vibrate`
(the actual vibration **motor**). The vocabulary landed as: your-turn = a motor
buzz lead-in + rising double (unlike any other cue), check/call = double-knock,
raise = increasing ramp capped by a motor buzz, all-in = crescendo + long motor
buzz, win/loss = OS notification buzz + a kick, heartbeat dub = a motor thump.
**Lesson:** on iOS, if a haptic feels weak it's because you're on `impact`; reach
for `vibrate`/`notification` for anything that should read as "felt."

## The four native desyncs (web is forgiving; a WKWebView is not)
The web rarely desyncs — the tab stays alive and refresh is a keystroke. Native
sessions desynced constantly, and the recovery hinged on weak points:

1. **Socket auth frozen at creation.** `createAuthedSocket` baked the bearer
   token into a static `auth: {token}`, which Socket.IO reuses on every reconnect
   — so once the short-lived token expired (e.g. while backgrounded), every
   reconnect re-handshook with a dead token and looped forever. Fix: pass `auth`
   as a *callback* so each attempt reads the current token, and refresh once on
   `connect_error`.
2. **No reliable resume signal.** Recovery hung off DOM `visibilitychange`, which
   is unreliable in a WKWebView. Added `@capacitor/app` and drove recovery off
   `appStateChange` (foreground).
3. **Frozen sequencer replaying stale beats.** iOS suspends JS timers while
   backgrounded; the hand-sequencer's `setTimeout` pump froze mid-runout and
   resumed replaying stale beats. Reset it on resume.
4. **30s missed-push stall.** The backstop after a human action was 30s; cut to
   10s (the first state push clears it, so it never churns during a normal orbit).

Toolchain gotcha worth its own memory: **`pod install` / `cap sync` fail on
`objectVersion = 70`** — Xcode 16's "recommended settings" bumps the project to a
format the bundled CocoaPods `xcodeproj` (knows 63 then jumps to 77, no 70) can't
read. Pinned the project to `objectVersion = 56`. If Xcode re-applies recommended
settings, it'll break again — reset to 56.

## The Mac/Docker gauntlet (three problems, one villain)

### 1. `database is locked` → it's the bind mount, not the DB
Under the cash-mode write burst, an uncaught `OperationalError("database is
locked")` 500'd a chat `save_message`. WAL + a 5s `busy_timeout` were already set,
so a writer was holding the lock >5s. A write benchmark inside the container told
the story: a 40KB game-state save took **9.56ms on the `./data` bind mount vs
0.26ms VM-local — ~37x slower**. The DB lives on Docker Desktop's VirtioFS host
bridge (`/run/host_mark/Users`, mount type `fakeowner`), whose fsync-heavy SQLite
commits are brutal. Fix (Mac-local, gitignored `docker-compose.override.yml`):
move the DB to a **named volume** in the VM's fast ext4. Homehub is native Linux,
so its bind mounts are fast — same code, no problem there. (Bonus: the clean
`docker compose stop` checkpointed a bloated 214MB WAL back into the main DB.)
We also hardened the chat-send path to degrade gracefully instead of 500ing.

### 2. The `seat debit refused` storm → a real chip mint, caught by instrumentation
Auditing the storm: the greedy seat-assignment core and the cold-start seed loop
both gate affordability correctly. The bug was in the *apply* loops that consume
`debit_bankroll_for_seat` — `_apply_stake_creations` ignored a `None` refusal and
created the `Stake` row anyway (an active loan with no principal moved = minted
chips). Hardened it to drop-on-fail, plus a loud alarm on the `to_seat` path.

That alarm immediately caught the real storm live (`slots_linda`, 13 refused
buy-ins in 8ms). Root cause: the lobby sweep **simulates a whole burst of hands
per table before any debit applies**, so a reloading AI sized *every* reload
against its full bankroll; the accumulated buy-ins over-committed one bankroll and
the later debits refused onto already-topped-up (already-persisted) seats →
minted chips. **Not fish-specific** — grinders rebuy too. Fix:
`_available_buyin_capacity` subtracts buy-ins already committed during the burst,
capping planned reloads at the real bankroll. Custody test proves a 5×140 reload
burst on a 200 bankroll plans exactly 200, not 700. **Lesson:** when you add a
"this should never happen" guard, log it loudly — ours found the bug within
minutes.

### 3. The mid-hand freeze → the dev server, through the Mac proxy
The game froze mid-hand on this Mac only — fine on homehub and prod. Ruled out
everything obvious: DB healthy, 16 CPUs / load 0.93 (not starved), no exceptions,
ticker not hot-looping. The tell: zero client socket activity for 30+ min while
the backend kept playing the hand — the phone's realtime socket had dropped and
wasn't recovering. The one thing this env has that native-Linux boxes and prod
don't: **Docker Desktop's port-forwarding proxy (vpnkit/gvisor)**. Dev pins
Socket.IO to **long-polling** (Werkzeug can't hold a WS upgrade), and that proxy
is flaky at holding long-lived polling. Fix: run the **prod-style server
locally** — `gunicorn -k geventwebsocket... ` + `SOCKETIO_ASYNC_MODE=gevent`
(the Dockerfile already ships it; dev just overrode the command). The built app
already negotiates WebSocket, and gevent's cooperative I/O stopped the worker from
blocking on LLM/DB calls. CPU dropped **~120% → ~22%** and the freeze was gone.
(WebSocket still doesn't upgrade through the proxy, but *polling-under-gevent* is
stable and cheap, which is the actual win.) Kept hot-reload via
`--reload --reload-engine=poll` since inotify doesn't propagate through VirtioFS.

**The meta-lesson of the whole afternoon:** when something breaks "only on the
Mac," suspect Docker Desktop's two soft spots — the **VirtioFS bind mount**
(filesystem/fsync) and the **vpnkit/gvisor proxy** (long-lived connections) —
before the application code.

## Odds and ends
- **In-game chat model:** the Default tier was on `openai/gpt-5-mini`; the in-game
  commentary/table-talk should run on the cheap/fast `groq/llama-3.1-8b-instant`
  like the rest of the dev flavor. Set env-local in the override
  (`DEFAULT_PROVIDER=groq`, `DEFAULT_MODEL=llama-3.1-8b-instant`) — no code/doc
  change, since it's a per-env dev default, not a product decision.
- **Native landing:** the app opens straight at `/login` (no marketing landing
  page inside an installed app); web unchanged.
- All Mac-specific knobs live in the **gitignored `docker-compose.override.yml`**
  (named DB volume, gevent server, groq default) so none of it leaks to homehub or
  prod.

## Still open (before TestFlight / prod)
- `Info.plist` ships dev-only `NSAllowsArbitraryLoads` — must go before release.
- Prod `CORS_ORIGINS` doesn't yet allow `capacitor://localhost` (REST + Socket.IO)
  — needed to point the native app at the prod backend.
- Free Personal Team signing expires ~7 days; rebuild to refresh.
