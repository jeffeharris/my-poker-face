---
purpose: Move the cash-mode lobby from read-driven world simulation to a realtime background ticker, with a per-user pace control and socket-pushed world events
type: design
created: 2026-05-24
last_updated: 2026-05-25
---

# Cash Mode — Realtime World Ticker

## Problem

The lobby world (AI movement + hand simulation at unseated tables) only
advances when `GET /api/cash/lobby` is read, and that read is driven by
the frontend's 8s poll loop in `Lobby.tsx`. The loop lives in the lobby
component and `clearInterval`s on unmount. Consequences:

1. **Frozen during play.** While the human is at their own table, the
   lobby component is unmounted, so nothing else in the world moves.
2. **Slow cadence.** Even in the lobby, the world advances once per 8s.
3. **Trickle throughput.** Each tick plays only 0–1 hands per table.

Net effect: the world feels sluggish, and there is no surface for
"interesting things are happening elsewhere" signals (a whale arriving,
an AI on a heater or on tilt) that could pull a player to another table.

## Goal

Keep the world progressing in realtime in the background — independent
of whether the player is in the lobby or seated at a table — and lay the
plumbing for pushed "world event" signals. Let the player set the pace.

## Architecture

### One shared ticker, not one-per-session

Production runs a **single gunicorn worker** (`-w 1`,
`GeventWebSocketWorker`); dev is single-process Flask. Because of the
GIL, sim work (pure-Python poker hands, ~227 hands/sec warm) serializes
on one core regardless of thread count. So:

- **One background ticker thread** loops over all *active* sandboxes per
  cycle. This naturally serializes SQLite writes (WAL = one writer), so
  there is no write-lock contention between concurrent tickers.
- **Time-budgeted, not hand-count-budgeted.** Each cycle spends at most
  `CYCLE_BUDGET_MS` across all active sandboxes, round-robining via a
  cursor so no sandbox is starved. Under load the world *slows
  gracefully* rather than saturating the core or starving foreground
  request handling.
- **Cooperative yield** (`socketio.sleep(0)`) between sandboxes so socket
  I/O and request handlers are never blocked for a whole cycle.

If concurrency ever outgrows a single worker, the next step is a Redis
distributed lock to elect a single ticker owner + a Socket.IO Redis
message queue — explicitly out of scope here.

### Presence drives "active"

The world is **sandbox-scoped** (one private casino per user), so the
ticker must know which sandboxes to tick. We track presence in-memory:

- `flask_app/services/presence.py` — registry keyed by `owner_id`,
  holding `{owner_id, sandbox_id, sids: set, last_seen}`.
- Socket `connect` marks a session active and joins the per-user lobby
  room `lobby:{owner_id}`; `disconnect` removes the sid.
- A session stays active while it has ≥1 live socket **or** was touched
  within `ACTIVE_TTL_SECONDS` (grace for the Lobby→Game navigation gap,
  and an HTTP-only fallback: `GET /api/cash/lobby` calls
  `presence.touch(...)`).
- "No persistence while inactive": once a user has no live sockets and
  the grace TTL lapses, their sandbox drops out of the tick set and the
  world stops advancing for them until they return.

### Ticker is the sole world-advancer

`refresh_unseated_tables` *advances* the world (plays hands, rolls
movement) — it is **not** idempotent. To avoid double-advancing, the
ticker becomes the single driver:

- When the ticker is enabled, `GET /api/cash/lobby` becomes a **pure
  read** (serialize current state) and only `presence.touch`es. It no
  longer calls `refresh_unseated_tables`.
- When the ticker is disabled (`WORLD_TICKER_ENABLED=false`), the lobby
  route keeps its legacy read-driven `refresh_unseated_tables` call, so
  the world still moves on read. This is the safety fallback.

The human's *own* active table is already skipped by
`refresh_unseated_tables` (`lobby.py` — `human_seat_index() is not None`)
and continues to be driven by the existing hand-boundary refresh hook.

### Pace control

Per-user setting `world_pace ∈ {subtle, lively, bustling}`, default
`lively`, settable from the lobby. Stored in a new `user_preferences`
table (migration v115). Pace maps to the per-tick `hand_sim_prob` (and
optionally cadence) the ticker passes to `refresh_unseated_tables`:

With a 2s base tick, the per-table mean interval between hands is
`(run_every * 2s) / hand_sim_prob`:

| pace | hand_sim_prob | run_every | ≈ per-table interval | feel |
|---|---|---|---|---|
| subtle | 0.15 | 3 | ~40s | ambient backdrop; world barely drifts |
| lively | 0.40 | 1 | ~5s | busy but followable (default) |
| bustling | 0.90 | 1 | ~2.2s | Vegas-floor churn |

Intervals are per table — the lobby's aggregate feed moves ~Ntables
faster. The wide spread is deliberate: subtle preserves narrative
continuity + signal scarcity (and suits the future in-game-notification
mode + mobile), while bustling trades legibility for a live-casino feel.
Pace also scales a user's share of the shared time budget — a bustling
user simply consumes more of it.

### Socket push

After ticking a sandbox, the ticker emits to `lobby:{owner_id}`:

- `lobby_tick` — lightweight nudge (`{sandbox_id, ts}`); the lobby UI
  debounce-refetches `/api/cash/lobby` when mounted.
- `world_event` — self-contained serialized `LobbyEvent`s (already
  sandbox-scoped, see `cash_mode/activity.py`) for toast-style signals.
  Because the payload is self-contained, a future in-game listener can
  surface "🐳 a whale just sat down at $5/$10" without a lobby fetch.

The `cash_mode/activity.py` ring buffer is already the designed event
surface ("scales without redesign" for background sim) and already
filters by `sandbox_id`, so no new event store is needed.

## Files

**Backend**
- `poker/repositories/schema_manager.py` — migration v115 (`user_preferences`).
- `poker/repositories/user_preferences_repository.py` *(new)* — get/set world_pace.
- `poker/repositories/__init__.py`, `flask_app/extensions.py` — wire `user_prefs_repo`.
- `flask_app/services/presence.py` *(new)* — active-session registry.
- `flask_app/services/ticker_service.py` *(new)* — the shared ticker loop.
- `flask_app/__init__.py` — start ticker once in `create_app()`.
- `flask_app/routes/game_routes.py` — socket connect/disconnect + lobby room.
- `flask_app/routes/cash_routes.py` — lobby read-only + touch; `PUT /api/cash/world-pace`; `world_pace` in payload.

**Frontend**
- `react/react/src/components/cash/api.ts` — `setWorldPace`, lobby `world_pace`.
- `react/react/src/components/cash/types.ts` — `WorldEvent`, `LobbyTick`, `WorldPace`.
- `react/react/src/components/cash/Lobby.tsx` — socket subscribe, pace selector, slow fallback poll.

## Out of scope (future)

- Event **detectors** (heater / tilt / whale classification) — only the
  push channel is built now.
- In-game (non-lobby) world-event toasts — channel is ready; wiring the
  Game view as a listener is a follow-up.
- Multi-worker scaling (Redis lock + Socket.IO message queue).
- Persisting world progression while the user is fully offline.
