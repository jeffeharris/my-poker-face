---
purpose: Investigation + fix for the live tournament "two hands flickering" report
type: guide
created: 2026-06-07
last_updated: 2026-06-07
---

# Two hands flickering in a live tournament (2026-06-07)

## The report

Jeff, mid-game in a production tournament: "it seems like there may be two active
tournaments with the same id — it's flashing back and forth between two hands."

## What it was NOT

Checked prod DB first to disprove the user's premise before chasing it. There was
exactly **one** tournament (`tourney_MBcuSAzWeIum0-mY`), **one** game row
(`tourney-wCgU4Up8yzvSLGcCj_1HSg`), **one** coherent table state (an 18-player /
3-table MTT; the human only ever plays their own table). No server-side
duplication. The flicker was entirely client-side.

## The actual chain

Two signals in the prod backend logs:

1. The WebSocket kept **closing with code 1002 (protocol error)** and
   auto-reconnecting (`reconnectionAttempts: Infinity`), clustered at hand
   boundaries (16:28:23, 16:36:34, 16:38:47).
2. **4 identical `GET /api/game-state/...` within 300ms** — multiple concurrent
   refreshers, i.e. more than one live socket for the same game_id.

Client defect (the flicker): `usePokerGame.ts`'s socket-init `useEffect` opened a
socket via `createSocket` but had **no teardown on re-run** — cleanup lived in a
*separate* unmount-only effect. Its dep array carried unstable callbacks
(`onGameCreated` etc.; `GamePage`'s `handleGameCreated` wasn't memoized). So a
re-run opened a **second socket and leaked the first** — still joined to the room,
still streaming `update_game_state` into the one shared Zustand store, each socket
running its own `connect → refreshGameState` and its own sequencer timeline. Two
pipelines writing one store ⇒ the UI oscillates between hand N and hand N+1. That
is the user's "two tournaments, same id, two hands."

The 1002 storm was the *amplifier*: each abnormal close drove an Infinity
reconnect, and the leak meant reconnects piled up sockets instead of replacing one.

## The 1002 root cause (separate from the client fix)

Prod runs `SOCKETIO_ASYNC_MODE=threading` (the default) **under the
`GeventWebSocketWorker`** gunicorn worker (confirmed via startup log:
`async_mode=threading; gevent monkey-patch active=True`). In threading mode,
python-engineio serves the WS transport with `simple_websocket`, but the worker
provides the geventwebsocket handler — a transport mismatch. `simple_websocket`'s
WS reads run on a gevent-patched socket and break at the protocol level on
close/upgrade → `simple_websocket.errors.ConnectionClosed: 1002`, followed by a
noisy `IndexError: tuple index out of range` in geventwebsocket's
`ignored_socket_errors` cleanup (harmless — the connection is already closing).

Caddy is a clean passthrough (`reverse_proxy poker-backend:5000` for
`/socket.io/*`, WS auto-handled) — not implicated.

The standards-aligned pairing — flagged in `config.py:56` (PRH-24) but never
operator-validated — is `SOCKETIO_ASYNC_MODE=gevent`, so engineio uses the
geventwebsocket transport the worker actually exposes.

## What shipped

Client fix (`react/react`):
- `usePokerGame.ts` socket-init effect re-keyed to **only `providedGameId`**, all
  callbacks read through refs, **real teardown** (disconnect + `resetSequencer`)
  on re-run/unmount, plus a belt-and-suspenders disconnect of any prior socket.
  Exactly one socket per game_id now; StrictMode-safe.
- Dropped the redundant initial `refreshGameState` (the `connect` handler is the
  single canonical fetch; its failure path now carries the load-failure UX).
- Memoized `GamePage.handleGameCreated`.
- tsc + lint + full vitest suite (266 passed / 2 skipped) green.

1002 remediation: **proposed, not applied** — set `SOCKETIO_ASYNC_MODE=gevent` in
prod env and validate the WS flow + redeploy. Held back because it's a prod
infra/env change. The client fix stops the flicker regardless of whether the
1002s persist.
