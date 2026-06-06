---
purpose: Compute/cost model for the backend and a staged, cheapest-first scaling blueprint that preserves the casual 1:1-sandbox-per-owner design
type: architecture
created: 2026-06-06
last_updated: 2026-06-06
---

# Scaling My Poker Face

How the backend consumes compute, what binds first as concurrent users grow, and
a staged path to scale — **without** compromising the casual "you are brought
into your own lived-in world" design. Grounded in the code as of schema v151
(launch, 2026-06-05). Update the stages as they land.

## The product constraint (do not violate)

Prod runs a **1:1 sandbox-per-owner** economy — every user gets their *own* world
of ~74 AI personas, tables, bankrolls, ledger, presence, renown. This stays
per-owner. A shared/global competitive world is off the table (it would wreck the
casual feel). Scaling preserves per-owner isolation rather than consolidating
worlds — and that isolation turns out to be a scaling *asset* (it shards cleanly
on `owner_id`; see Stage 4).

## Current state (single small box)

- **Web tier:** Flask + `gunicorn -k geventwebsocket… -w 1` — a **single gevent
  worker** (cooperative concurrency on one core). `docker-compose.prod.yml`.
- **Locked at `-w 1`:** the world ticker + presence are in-process singletons
  (`flask_app/services/presence.py:20-23`, `OPS_RUNBOOK §7`/PRH-10). `-w 2` would
  run two tickers → double-sim + races. Redis is deployed (rate-limiter only today).
- **World ticker** (`flask_app/services/ticker_service.py`): in-process loop;
  ticks **only active sandboxes** (`presence.active_sessions()`); `CYCLE_BUDGET_MS
  = 250` per 2 s tick; round-robin rotation; `MAX_ACTIVE_SANDBOXES_PER_CYCLE = 50`.
  The off-screen sim is **rule-based — no LLM** (`cash_mode/controller_cache.py`:
  "full sim never invokes the LLM").
- **State:** in-memory resident caches (`flask_app/services/game_state_service.py`:
  `games`, `*_locks`, `game_last_access`, 2 h idle eviction). Persistence =
  **SQLite single-writer + WAL** (`poker/repositories/base_repository.py`),
  saved after each action. All subsystems share one DB (`schema_manager.py`, v151).
- **Per-process RAM baseline ≈ 550 MB** (measured steady-state RSS, flat over
  35 min — not a leak). It's the Python process + eval7 + imports; **the strategy
  lookup tables are lazy-loaded (~20–50 MB parsed), not the baseline.** Paid *per
  worker*.
- **LLM:** budget kill-switches (`core/llm/budget.py`): `$5/owner/day` +
  `$50/day` global; cosmetic calls (chatter/commentary/narration/avatars) shed
  first; the default `sharp` bot is **LLM-free for decisions**. Bounded by design,
  not a perf bottleneck.

## Cost model

### Per active sandbox-tick (background world sim)

| Work | Cost / 2 s tick |
|---|---|
| Rule-based hands (4 tables, ~40% fire), in-memory solver lookups, no I/O | ~2–5 ms |
| Lobby refresh DB writes (bankroll, cash_tables, events) | ~3–10 ms |
| Renown-v2 field score + per-AI batch write (every 5 min, ~74 rows) | ~3–8 ms (periodic) |
| Holdings snapshot (every 10 min) | ~5–15 ms (periodic) |
| **Per-sandbox tick** | **~5–20 ms** |

The sim is **cheap and self-throttling**: total ticker CPU is hard-capped at
~250 ms per 2 s (~12.5% of one core) regardless of user count. More active users
→ each world evolves slightly slower (round-robin), **not** more box CPU. It
costs **~$0 in LLM**. It only runs while a user is **active** (idle/offline
worlds aren't ticked) — so cost scales with *concurrent active* users, not
registered users.

### Foreground (a live hand)

- Equity Monte Carlo (`poker/decision_analyzer.py`, `DECISION_ANALYSIS_ITERATIONS=500`):
  **~5 ms CPU per AI decision**, synchronous, does **not** yield under gevent.
- LLM expression/commentary (if enabled): 1–3 s latency, **yields** cooperatively
  (non-blocking). Default `sharp` decisions are LLM-free.
- SQLite save after each action: ~2–5 ms.

### What binds first (in order)

1. **Single-worker cooperative CPU** — the ticker + foreground equity-MC bursts +
   SQLite writes all share one gevent core; the ~5 ms MC bursts don't yield.
   Degradation (lobby-tick lag, sluggish hands) appears around **~15–25
   concurrent active users**.
2. **SQLite single-writer** — WAL allows concurrent reads; the bind is ticker
   lobby-writes contending with foreground game saves. `retry_on_lock` absorbs
   brief contention. Painful around **~30+** concurrent active users.
3. **RAM — last, and asymmetric.** ~550 MB baseline + ~5–15 MB per live game.
   Headroom is fine for one worker; **each *additional* worker adds another
   ~550 MB baseline** — so RAM, not CPU, caps worker count on a small box.

## Staged scaling blueprint (cheapest-first)

| Stage | Move | Effort | Removes |
|---|---|---|---|
| **1A** | Memoize strategy tables in `tiered_factory.py` | ~2 h, ~0 risk | per-game-start JSON re-reads |
| **1B** | Extract ticker → own process + Redis presence + Socket.IO `message_queue` | 3–5 d | the `-w 1` lock (PRH-10) |
| **2** | Web tier `-w 2+` (needs RAM) | 1–2 d | single-core foreground ceiling |
| **3** | Bigger box (CPX21 4 GB ≈ +€6/mo) + `-w 3` | ~1 h, no code | RAM cap on workers — **best ROI** |
| **4** | Per-owner sandbox sharding (consistent-hash `owner_id`) | 1–2 wk | horizontal ceiling / SPOF |
| **5** | SQLite → Postgres (per-sandbox schemas) | 2–4 wk | concurrent-write limit |

### Stage 1A — memoize strategy tables (do now)
`flask_app/handlers/tiered_factory.py` calls `load_strategy_table()` /
`load_hu_strategy_table()` / `load_depth_strategy_tables()` /
`load_archetype_preflop_tables()` on **every** `build_tiered_controller()` (i.e.
every cold game start/restore), re-reading JSON from disk. Memoize at module level
behind a lock, mirroring `cash_mode/full_sim.py:340-366`. Tables are immutable →
safe to share. Removes ~20–50 ms of cold-start latency and the per-start
filesystem reads. Zero risk.

### Stage 1B — extract the world ticker (the keystone)
The only thing forcing `-w 1` is that presence + ticker are in-process singletons.
- Add a `RedisPresenceStore` (`presence.py`) behind `PRESENCE_REDIS_ENABLED`
  (in-memory stays as the default/fallback — no regression). Web workers write
  presence to Redis on connect/disconnect/touch; the ticker reads it.
- New `flask_app/services/ticker_worker.py`: `create_app()` to wire repos, then
  `start_world_ticker(socketio)` and park. Run as a dedicated `ticker` service in
  compose (one elected ticker).
- Guard `start_world_ticker()` in `flask_app/__init__.py` behind
  `TICKER_PROCESS_EXTERNAL != '1'` so the web workers stop running their own.
- Add `message_queue=REDIS_URL` to the `SocketIO(...)` ctor (`flask_app/extensions.py`)
  so the ticker process can emit to clients on the web workers via Redis pub/sub.
- **Highest-risk piece:** the sandbox seat-mutation lock
  (`game_state_service.get_sandbox_lock`) is in-memory. Across processes, the
  ticker (touches *unseated* tables via `refresh_unseated_tables`) and web workers
  (human `sit` on *seated* tables) barely overlap — option (a) accept that
  near-disjoint split initially; option (b) Redis `SET NX PX` distributed lock.
  Confirm the overlap is narrow before relying on (a).

Develop entirely behind the flag (off) without touching prod.

### Stage 2 — multi-worker web tier
After 1B: bump `-w 1` → `-w 2+`. Requires `message_queue=REDIS_URL` (Stage 1B)
for cross-worker Socket.IO, and **sticky sessions** (Caddy `ip_hash` / cookie) so a
live game stays on its worker (game state remains per-worker; no need to move it
to Redis). **RAM gate:** 2 workers × ~550 MB > the current 1200 MB cap — raise
`mem_limit` or do Stage 3 first.

### Stage 3 — bigger box (best ROI)
Hetzner CPX21 (4 GB, ~+€6/mo) → `mem_limit` headroom for `-w 3` = ~3× foreground
throughput. Pure ops change (`docker-compose.prod.yml` + Hetzner resize). Likely
carries to ~50 concurrent active users.

### Stage 4 — per-owner sandbox sharding
Consistent-hash on `owner_id` → each user pinned to a shard (box/container group)
running its own backend + ticker + DB slice. No cross-shard coordination because
worlds never share state — the 1:1 design *is* the shard key. Obstacle: splitting
the single SQLite file across shards (migrate a user's rows). Worth it at ~50+
concurrent.

### Stage 5 — SQLite → Postgres
For true concurrent writes / multi-box. Per-`sandbox_id` Postgres schemas express
the per-owner isolation cleanly. Migration surface is large: 151 SQLite-specific
migrations in `schema_manager.py`, the `sqlite3` usage in `base_repository.py`.
**Defer until Stage 3 is exhausted** — SQLite+WAL+`retry_on_lock` is robust well
past current traffic.

## Near-term recommendation

- **Now:** Stage 1A (2 h win) + start Stage 1B's `RedisPresenceStore` behind a
  default-off flag (zero prod risk to build).
- **First real scale-out (when sustained concurrent active users cross ~15):**
  ship 1B + 2 + the 4 GB box together. Sequence: deploy RedisPresenceStore (flag
  off) → add Socket.IO `message_queue` → flip `PRESENCE_REDIS_ENABLED` → deploy the
  `ticker` service → set `TICKER_PROCESS_EXTERNAL=1` on the backend → raise
  `mem_limit` + `-w 2` (+ box upgrade).

## What NOT to do

- **Don't share the world** — it breaks the casual design and isn't needed (the
  per-user sim is ~free and self-throttling).
- **Don't jump to Postgres early** — exhaust Stage 3 first; the migration surface
  is large and SQLite handles current load comfortably.
- **Don't add workers without RAM** — each worker pays the ~550 MB baseline; 2 on
  the current 1.9 GB box exceeds the budget.
- **Don't deploy from a dev box** — manual `./deploy.sh` from a laptop has bitten
  prod (local DB sidecars clobbering prod's WAL, file-mode/perm drift). Use the CI
  pipeline (clean checkout + `chown` + `data/`-excluded rsync).

## Key files

| Concern | File |
|---|---|
| Worker config / compose | `docker-compose.prod.yml` |
| World ticker | `flask_app/services/ticker_service.py` |
| Presence (singleton today) | `flask_app/services/presence.py` |
| In-memory game cache | `flask_app/services/game_state_service.py` |
| SQLite repo / WAL / retry | `poker/repositories/base_repository.py` |
| Strategy table loads (Stage 1A) | `flask_app/handlers/tiered_factory.py` |
| Memoization pattern to mirror | `cash_mode/full_sim.py:340-366` |
| Equity MC | `poker/decision_analyzer.py` |
| LLM budget | `core/llm/budget.py` |
| Ops runbook / `-w 1` rationale | `docs/guides/OPS_RUNBOOK.md` (§7, PRH-10) |
