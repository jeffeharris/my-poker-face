---
purpose: File-by-file implementation plan for Stage 1B — extract the world ticker into its own process + Redis-backed presence + Socket.IO Redis message_queue, to break the -w 1 lock
type: guide
created: 2026-06-06
last_updated: 2026-06-06
---

# Scaling Stage 1B — ticker extraction + Redis presence

Goal: break the single-worker (`-w 1`) lock so the web tier can run `-w 2+`
(target: ~20 concurrent active users on **one** box; SQLite stays). The only
thing forcing `-w 1` today is that the **world ticker** and **presence** are
in-process singletons (`flask_app/services/presence.py:20-23`, OPS_RUNBOOK §7).

Stage 1B = move presence to **Redis**, give Socket.IO a **Redis `message_queue`**
(so a separate process can emit to clients on the web workers), and run the
ticker as its **own dedicated process**. Everything ships behind **default-off
flags** with the current in-memory path as a no-regression fallback. See the
broader context in `docs/SCALING.md`.

## Flags (all default OFF)
| Flag | Effect |
|---|---|
| `PRESENCE_REDIS_ENABLED` | presence reads/writes go to Redis instead of the in-memory `_sessions` dict |
| `SOCKETIO_REDIS_MQ_ENABLED` | adds `message_queue=REDIS_URL` to the `SocketIO(...)` ctor (cross-process emit) |
| `TICKER_PROCESS_EXTERNAL` | web workers STOP running the in-process ticker (the dedicated process owns it) |
| `TICKER_WORKER_PROCESS` | set only in the ticker process; guards boot hooks (e.g. `kill_all_cash_sessions`) from double-running |

## File-by-file changes

1. **`flask_app/services/presence.py`** — refactor into `InMemoryPresenceStore`
   + `RedisPresenceStore` + a `_get_backend()` dispatch on `PRESENCE_REDIS_ENABLED`.
   Keep every public function signature identical. Redis key design:
   `presence:active_owners` (ZSET scored by `last_seen` for fast active enumeration
   + TTL expiry) + `presence:session:{owner_id}` (HASH: sandbox_id, sids, last_seen).
   `active_sessions()` must **pipeline** the ZRANGEBYSCORE + per-owner HGETALL/SMEMBERS
   (one batch, not N round-trips). Add a TTL/heartbeat (`ACTIVE_TTL_SECONDS`) so
   stale owners expire (in-memory presence relied on process state; Redis needs
   explicit expiry + a touch cadence). `RedisPresenceStore.__init__` takes an
   optional `redis_client` (tests inject `fakeredis`).

2. **`flask_app/services/ticker_worker.py`** (NEW) — dedicated entrypoint.
   `gevent monkey.patch_all()` as the **absolute first statement** (before any
   import), then `create_app()` to wire repos/extensions, then
   `start_world_ticker(socketio)` and park. Must set `TICKER_WORKER_PROCESS=1`
   before `create_app()` so boot hooks don't double-run. Serves no web routes.

3. **`flask_app/__init__.py`** (~lines 186-212) — guard `start_world_ticker()`
   behind `TICKER_PROCESS_EXTERNAL != '1'` (web workers skip it when the dedicated
   process is on); guard `kill_all_cash_sessions` (and any other one-shot boot
   sweep) behind `TICKER_WORKER_PROCESS`. Log `"[TICKER] skipped in-process ticker
   (TICKER_PROCESS_EXTERNAL=1)"` so rollout is verifiable.

4. **`flask_app/extensions.py`** (~lines 52-55) — `_get_socketio_message_queue()`
   returning `REDIS_URL` when `SOCKETIO_REDIS_MQ_ENABLED`, else `None`; pass it to
   `SocketIO(message_queue=...)`. (Cross-process emit via Redis pub/sub — the
   standard Flask-SocketIO pattern; works under the gevent-websocket worker.)

5. **`flask_app/config.py`** — add the four flags as readable constants
   (`os.environ.get(...)`), matching existing patterns.

6. **`docker-compose.prod.yml`** — add a `ticker` service: same image/build, same
   `data` volume + env as `backend`, plus `TICKER_PROCESS_EXTERNAL=1`,
   `TICKER_WORKER_PROCESS=1`, `PRESENCE_REDIS_ENABLED=1`, `SOCKETIO_REDIS_MQ_ENABLED=1`;
   command `python -m flask_app.services.ticker_worker`; `restart: unless-stopped`;
   **no published port**. `docker-compose.yml` (dev) — same under `profiles: [ticker]`.

7. **`requirements-dev.txt`** — add `fakeredis>=2.0.0` (pin `>=2.20.0` if redis 7.x
   compat issues). No change to `requirements.txt` (redis already present).

8. **`flask_app/services/game_state_service.py:177-193`** — `get_sandbox_lock` is an
   in-memory `threading.Lock`. See the sandbox-lock decision below; at minimum add
   a TODO documenting the cross-process upgrade path.

## The sandbox-lock decision
`get_sandbox_lock` guards seat mutations. After extraction, the ticker process and
web workers are separate processes, so an in-memory lock no longer coordinates
them. The ticker only mutates **unseated** tables (`refresh_unseated_tables`); the
human `sit`/seat routes mutate **seated** tables — the overlap is narrow.
- **Option (a), recommended initially:** accept the near-disjoint split; document
  it. Verify the overlap really is narrow by tracing the sit path
  (`game_handler` / `cash_routes`) vs `refresh_unseated_tables`.
- **Option (b), if overlap is real:** Redis distributed lock (`SET key NX PX 5000`)
  at the specific seat-claim call sites.

## Build sequence
- **Phase 0:** add flags to `config.py`; add `fakeredis` to dev reqs; add the
  sandbox-lock TODO.
- **Phase 1:** `RedisPresenceStore` (flags off → no prod effect). Existing
  `tests/test_presence.py` must still pass unchanged; add `tests/test_redis_presence.py`
  (fakeredis, mirrors the in-memory behavioral tests).
- **Phase 2:** Socket.IO `message_queue` + `ticker_worker.py` + the `__init__.py`
  guards. Smoke: `import flask_app.services.ticker_worker` succeeds; `--quick` green.
- **Phase 3:** compose wiring (ticker service prod + dev profile).
- **Phase 4:** local integration with flags on (`docker compose --profile ticker
  up`): verify `lobby_tick` + `world_event` arrive via the ticker process; check
  `"skipped in-process ticker"` in backend logs.
- **Phase 5:** prod rollout (below).
- **Phase 6 (separate PR = Stage 2):** `-w 2` + raise `mem_limit` + confirm Caddy
  sticky sessions.

## Zero-downtime rollout (flag-flip order) + rollback
1. **Deploy code, all flags OFF** — zero behavioral change. *Rollback:* revert + redeploy.
2. **`SOCKETIO_REDIS_MQ_ENABLED=1`**, restart backend — emit now routes via Redis
   pub/sub (consumer still same process). Verify lobby_tick still arrives.
3. **`PRESENCE_REDIS_ENABLED=1`**, restart backend — in-process ticker now reads
   presence from Redis; workers write it. Verify
   `redis-cli ZRANGE presence:active_owners 0 -1` shows your owner after opening the lobby.
4. **Start the `ticker` service.** ⚠️ Backend still runs its own ticker until step 5
   → **double-tick window**. Do 4→5 back-to-back (seconds).
5. **`TICKER_PROCESS_EXTERNAL=1` on backend**, restart — in-process ticker stops;
   dedicated process is sole ticker. Verify the "skipped in-process ticker" log +
   ticker-container "world ticker started" + lobby_tick/world_event still arrive.

Each step is independently reversible by removing its flag + restarting. Steps 4+5
roll back together: `stop ticker`, unset `TICKER_PROCESS_EXTERNAL`, restart backend.

## Test plan
- `tests/test_redis_presence.py` (new, fakeredis): mark_active/inactive (within +
  after TTL grace), touch, multi-tab (multiple sids per owner), multiple owners,
  clear. Interface-equivalent to `test_presence.py`.
- `tests/test_presence.py`: unchanged (defaults to in-memory; `PRESENCE_REDIS_ENABLED=0`).
- `scripts/test_1b_integration.py` (manual, not CI): socket client joins a lobby
  room → assert `lobby_tick` arrives; then restart with the dedicated ticker and
  assert ticks still arrive via the Redis bridge.

## Risks / unknowns (verify during implementation)
1. **`kill_all_cash_sessions` double-run** — the `TICKER_WORKER_PROCESS` guard
   needs discipline; consider making it an explicit `create_app()` param instead of env.
2. **gevent monkey-patch ordering** in `ticker_worker.py` must be first statement
   or the Redis listener can deadlock.
3. **`active_sessions()` must be pipelined** (O(1) round-trips), not N sequential calls.
4. **`socketio.start_background_task` under `message_queue`** in the ticker process —
   confirm the background task runs (look for "world ticker started" in ticker logs).
5. **fakeredis ↔ redis 7.x** compat — verify in Phase 1.
6. Redis key namespace: presence uses `presence:*`, rate-limiter uses `RATELIMIT_*` —
   no collision (verify with `KEYS presence:*`).
7. The ticker's other in-process state (`_last_marker`, `_last_snapshot_at`,
   `_last_prestige_at`, `_last_watchdog_at`, `_last_payout_reconcile_at`) is fine for
   ONE ticker process; revisit only if HA (two tickers) is ever added.

## Key files
| Purpose | File |
|---|---|
| Presence backend (modify) | `flask_app/services/presence.py` |
| Ticker worker (create) | `flask_app/services/ticker_worker.py` |
| App factory guards (modify) | `flask_app/__init__.py` |
| SocketIO message_queue (modify) | `flask_app/extensions.py` |
| Flags (modify) | `flask_app/config.py` |
| Prod/dev compose (modify) | `docker-compose.prod.yml`, `docker-compose.yml` |
| Dev reqs (modify) | `requirements-dev.txt` |
| Sandbox lock (TODO/decision) | `flask_app/services/game_state_service.py:177` |
| New tests | `tests/test_redis_presence.py` |
