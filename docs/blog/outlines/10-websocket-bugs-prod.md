---
purpose: Ready-to-write outline for the "WebSocket bugs you only see in production" blog post (Devlog track)
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline: WebSocket bugs you only see in production

- **Working title:** WebSocket bugs you only see in production
- **Track:** Devlog
- **Target reader:** Build-in-public followers and working developers who ship real-time / Socket.IO apps. Secondary: anyone who has shipped something that passed every test locally and then misbehaved only under a real load balancer with real reconnects.

## Hook (one line)

A player reported "two tournaments with the same id, flashing back and forth between two hands" — and the real cause was a single missing cleanup function in a React effect, amplified by a WebSocket misconfiguration that had been silently throwing protocol errors in production for who knows how long.

## Narrative spine (section beats, in order)

1. **The report, in the user's own words.** Mid-game in a live production tournament, the founder hit something that looked impossible: the table was flickering between two different hands. The first instinct ("there are two tournaments with the same id") was a reasonable guess — and it was wrong. Open on the verbatim bug report because it sets up the whole investigation: the symptom pointed at the server, the bug was on the client.

2. **Disprove the user's premise before chasing it.** The first move was to check the prod database, not the code — and it showed exactly one tournament, one game row, one coherent 18-player / 3-table state. No server-side duplication at all. The flicker was 100% client-side. (This is a recurring discipline in the project: verify the premise with a measurement before you start fixing the thing the report *describes*.)

3. **Two signals in the prod logs — neither of which you'd see locally.** The backend logs showed the WebSocket closing with **code 1002 (protocol error)** and auto-reconnecting forever (`reconnectionAttempts: Infinity`), clustered right at hand boundaries. And a tell-tale: **4 identical `GET /api/game-state` calls within 300ms** — proof that more than one live socket was attached to the same game. Two independent failures had braided together.

4. **The actual bug: a React effect with no teardown.** The socket-init `useEffect` in `usePokerGame.ts` opened a socket but its cleanup lived in a *separate* unmount-only effect, and its dependency array carried unstable (un-memoized) callbacks. So every re-run opened a **second** socket and leaked the first — both still joined to the room, both streaming `update_game_state` into one shared Zustand store, each running its own sequencer timeline. Two pipelines writing one store ⇒ the UI oscillates between hand N and hand N+1. That is, precisely, "two tournaments, two hands."

5. **The amplifier: a transport mismatch that had been failing quietly.** Production ran `SOCKETIO_ASYNC_MODE=threading` (the default) *under* the gevent-websocket gunicorn worker — a mismatch where python-engineio serves the socket with `simple_websocket` but the worker exposes the geventwebsocket handler. The result was the 1002 storm. Crucially, the existing startup health-check only verified gevent monkey-patching was *active* (it logged `active=True`) and stayed silent through the entire storm — a check with a blind spot exactly where the bug lived.

6. **Two fixes, deliberately split.** The client fix (one socket per game_id, real teardown, callbacks read through refs, StrictMode-safe) stops the flicker *regardless* of whether the 1002s persist — so it shipped immediately. The infra fix (`SOCKETIO_ASYNC_MODE=gevent`) was **proposed, not applied** in the same change, because it's a production env change that needs its own validation and redeploy. Worth being honest about: the diagnosis names the fix; shipping it is a separate, slower step.

7. **Defense-in-depth: fix the symptom too, not just the source.** The follow-up hardening pass added a process-global **state-version counter** stamped on every state push, so a stale frame from *any* leaked or orphaned socket gets dropped by the client — the leak fix removes the source, this removes the symptom no matter how many sockets leak. Plus the startup checks that would have caught the 1002 (transport-pairing + multi-worker), per-connection anonymous rate-limit keying, idle-key pruning, and a default socket error handler. The lesson isn't "we fixed a bug," it's "the class of bug stays cheap to survive."

## Evidence & assets

**Hard facts / numbers to cite (all from the two captain's-log entries, verifiable):**
- WebSocket **close code 1002 (protocol error)**, auto-reconnecting with `reconnectionAttempts: Infinity`, clustered at hand boundaries (logged timestamps 16:28:23, 16:36:34, 16:38:47). (`two-hand-flicker.md`)
- **4 identical `GET /api/game-state/...` within 300ms** — the fingerprint of more than one live socket on one game_id. (`two-hand-flicker.md`)
- Production state at the time: **one** tournament, **one** game row, **one** coherent **18-player / 3-table** MTT — server-side duplication disproven by DB query. (`two-hand-flicker.md`)
- Root cause of 1002: `SOCKETIO_ASYNC_MODE=threading` running under the `GeventWebSocketWorker` (`simple_websocket` vs geventwebsocket transport mismatch), confirmed via startup log `async_mode=threading; gevent monkey-patch active=True`. Standards-aligned pairing is `SOCKETIO_ASYNC_MODE=gevent`. (`two-hand-flicker.md`; flagged in `config.py:56` / PRH-24)
- Client fix validation: **tsc + lint + full vitest 266 passed / 2 skipped** (later 271 passed / 2 skipped after the hardening pass added `gameStore.version.test.ts`). (`two-hand-flicker.md`, `websocket-hardening.md`)
- Hardening adds: process-global state-version guard (`flask_app/state_version.py`, atomic via `itertools.count`), transport-pairing + multi-worker startup ERROR checks, per-`request.sid` anonymous rate-limit keying, 5-min idle-key sweep, `@sio.on_error_default`. (`websocket-hardening.md`)
- Supporting context: Socket.IO rate-limiting is **per-process in-memory**, not shared across workers — which is exactly why the multi-worker startup check matters. (`RATE_LIMITING.md`, §"Socket.IO rate limiting")

**Screenshots / files to include:**
- No existing screenshot captures this bug directly (it's a flicker — a still frame won't show it). Options: (a) a short screen-recording GIF of the flicker if one can be reproduced/was captured (founder to confirm one exists — see Open gaps); (b) a redacted snippet of the prod log showing the 1002 close + the 4-in-300ms `GET /api/game-state` burst (high-credibility, low-effort, this is a Devlog post); (c) a small before/after code diff of the `usePokerGame.ts` effect (the missing teardown is the load-bearing detail and reads well inline).
- `react/react/src/assets/screenshots/desktop-table.png` — neutral establishing shot of the live table, only if a visual is wanted up top. Not essential for a Devlog post.

**Commits / files to reference:**
- Captain's-log entries are the spine: `docs/captains-log/bug-fix-tournament/2026-06-07-two-hand-flicker.md` and `docs/captains-log/websocket-review/2026-06-07-websocket-hardening.md`.
- The PR(s) behind these — the bug-fix-tournament transcript ends with "commit this and open a PR" / "watch CI and merge when green", so a merged PR exists. Pull the exact PR number + the client-fix commit SHA from the game repo before publishing (see Open gaps). MEMORY index references PR #233 area / the two-hand-flicker fix landing in main.
- `flask_app/state_version.py`, `usePokerGame.ts`, `flask_app/socket_rate_limit.py`, `config.py:56` (PRH-24) as the named code touchpoints.

## Candidate pull-quotes (verbatim)

1. *"in the game I'm playing in production, i am in a tournament but it [seems] like there may be two active tournaments with the [same] id? it's flashing back and [forth] between two hands."* — the founder's original bug report, verbatim from the `bug-fix-tournament` transcript (typos preserved; bracketed corrections optional). This is the perfect cold open: the wrong hypothesis stated confidently, which the post then dismantles.
2. *"Checked prod DB first to disprove the user's premise before chasing it."* — `two-hand-flicker.md`. The methodology in one sentence.
3. *"The leak fix removes the *source*; this removes the *symptom* regardless of how many sockets leak."* — `websocket-hardening.md` (on the state-version guard). The defense-in-depth thesis.

## Draft intro paragraph (post voice)

I was playing a tournament on the production site when the table started doing something that shouldn't be possible: flickering back and forth between two different hands. My first thought — typed straight into the bug report — was that there were somehow two tournaments running under the same id. That was wrong, but it was wrong in a useful way. The first thing I did wasn't open the code; it was query the production database, which showed exactly one tournament, one game, one coherent table. So the duplication wasn't on the server. It was a single missing cleanup function on the client, dressed up by a WebSocket misconfiguration that had been throwing protocol errors in production the whole time without anyone noticing — the kind of bug you simply cannot see on localhost.

## Open gaps (need the founder, or more reporting)

- **Is the 1002 / `SOCKETIO_ASYNC_MODE=gevent` fix actually deployed to prod yet?** Both logs say the infra change was *proposed, not applied*. The post should state the current prod status truthfully — "client fix is live, transport fix is [pending / shipped on DATE]." Only the founder can confirm whether the env change has since been rolled out and validated.
- **Exact PR number(s) and commit SHA(s).** The transcript proves a PR was opened and merged on green, but the numbers live in the game repo, not this marketing repo. Confirm before citing. (MEMORY references the fix landing near PR #233 / two-hand-flicker but that needs verification, not assertion.)
- **Is there a usable visual?** A short recording of the actual flicker would be the ideal asset; if none was captured, a redacted prod-log snippet is the fallback. Founder to confirm what exists.
- **How long had the 1002s been happening before this report?** The logs prove it was happening at the time; they don't establish how far back. Don't claim a duration — say "had been failing quietly" and leave it there unless the founder can date it.
- **Scope honesty:** this is one bug story, not a survey of all WebSocket failure modes. Keep the title's "bugs you only see in production" anchored to *this* concrete case (leaked socket + transport mismatch + a health-check blind spot) rather than over-generalizing into a listicle.

## Cross-links (other posts in the series)

- The **June 5 production-cutover post** (the "four confident misdiagnoses, each resolved only by reproducing or measuring" story) — this post is the same discipline applied to a different bug, two days later. The "disprove the premise before chasing it" beat is the explicit through-line; link the two as a pair on *measure-don't-guess*.
- A **rate-limiting / infra Devlog post** if one exists — section 7's per-`request.sid` keying and the multi-worker startup check connect to `RATE_LIMITING.md`.
- The broader **"production on Hetzner" / deploy-and-ops** thread — the transport mismatch is a Caddy-passthrough-is-fine, app-config-is-wrong story, which fits an ops-lessons post.
- Any post on **working with the AI pair** — the transcript shows the founder steering tightly here ("commit this and open a PR", "watch CI and merge when green") while the diagnosis work was delegated; a small, honest data point for a "how the pairing actually divides labor" post.
