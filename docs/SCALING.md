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

> **Reviewed 2026-06-06 (Codex, code-grounded) — two corrections folded in:**
> (1) **`gunicorn -w 2` does NOT work** with `GeventWebSocketWorker`: Flask-SocketIO
> can't run multiple workers under one gunicorn master (no inter-worker sticky
> routing). The real horizontal path is multiple **single-worker *containers*** behind
> a sticky LB + Redis `message_queue` — never `-w 2`.
> (2) The ticker **can't simply move to its own process**: it reads web-worker-local
> game memory (`live_cash_seated_pids()` to avoid reusing personas in live hands;
> the stale-session watchdog) and races the human-`sit` path on the same
> `cash_tables` seat JSON. So 1B has **hard prerequisites** and drops below the cheap
> tuning wins.

| Stage | Move | Effort | Removes |
|---|---|---|---|
| **0 — tuning** | Relieve the single worker (knobs below), keep `-w 1` | ~1 d | foreground contention — **likely enough for 20** |
| **1A** ✅ done | Memoize strategy tables in `tiered_factory.py` | ~2 h | per-game-start JSON re-reads |
| **3** | Bigger box (CPX21 4 GB ≈ +€6/mo) | ~1 h, no code | RAM/CPU headroom — **best ROI** |
| **1B** | Extract ticker → own process + Redis presence + Socket.IO MQ — **has prerequisites (below)** | 1–2 wk | the `-w 1` lock |
| **2** | Multiple single-worker *containers* + sticky Caddy + Redis MQ (**not** `-w 2`) | 3–5 d | single-process foreground ceiling |
| **4** | Per-owner sandbox sharding (consistent-hash `owner_id`) | 1–2 wk | horizontal ceiling / SPOF |
| **5** | SQLite → Postgres (per-sandbox schemas) | 2–4 wk | concurrent-write limit |

**For the 20-user target, Stage 0 + (if needed) Stage 3 is very likely sufficient — without 1B/2 at all.**

### Stage 0 — tune the single worker (do first, for 20 users)
Keep `-w 1`; just reduce what competes for the one gevent core. Low risk, mostly env/config:
- **`DECISION_ANALYSIS_ITERATIONS`** (env, prod=500; `decision_analyzer.py:981` even notes lowering it "raises the concurrent-hands ceiling") → drop to ~200–300. Directly cuts the per-AI-decision equity-MC CPU burst (the main non-yielding foreground cost).
- **`SOCKETIO_ASYNC_MODE=gevent`** (env, currently `threading` — `config.py:63`) → validate in prod; better cooperative yielding under the gevent-websocket worker.
- **Ticker pacing** (`ticker_service.py`): lower `WORLD_TICKER_MAX_SANDBOXES` (env, default 50) and make `CYCLE_BUDGET_MS` (const 250 ms, line 42) + the ~2 s tick interval env-tunable, then give the foreground more core (slower world is fine — casual).
- **Off-screen narration → templated** (`vice_use_llm_narration` / `hustle_use_llm_narration` in `cash_mode/lobby.py`, default True; the `False` path already exists) → removes the ticker's LLM calls/latency.
- Already shipped: Stage 1A memoization + the `mem_limit` cap.

### Stage 1A — memoize strategy tables (do now)
`flask_app/handlers/tiered_factory.py` calls `load_strategy_table()` /
`load_hu_strategy_table()` / `load_depth_strategy_tables()` /
`load_archetype_preflop_tables()` on **every** `build_tiered_controller()` (i.e.
every cold game start/restore), re-reading JSON from disk. Memoize at module level
behind a lock, mirroring `cash_mode/full_sim.py:340-366`. Tables are immutable →
safe to share. Removes ~20–50 ms of cold-start latency and the per-start
filesystem reads. Zero risk.

### Stage 1B — extract the world ticker (only after prerequisites)
Moving the ticker to its own process is what eventually breaks the `-w 1` lock,
but it is **not** mechanical. Full plan + risks: `docs/plans/SCALING_STAGE_1B.md`.
**Hard prerequisites (must land first):**
1. **Decouple the ticker from web-worker memory.** `_tick_sandbox` feeds
   `refresh_unseated_tables` from `live_cash_seated_pids()` (avoid reusing personas
   in *live* hands) and the stale-session watchdog reads local `game_state_service.games`.
   A separate process can't see those → live-table corruption. Replace those reads
   with durable DB/session-state queries first.
2. **Cross-process seat lock.** Human `sit` starts from an *unseated/open* table and
   races the ticker's live-fill on the **same `cash_tables` seat JSON**
   (`cash_routes.py` vs `ticker_service.py` `refresh_unseated_tables`). The in-memory
   `get_sandbox_lock` won't coordinate two processes — needs a Redis `SET NX PX`
   lock on all seat-mutation paths. (The earlier "near-disjoint, defer it" take was
   wrong.)
3. **No double-tick on rollout.** World mutation is non-idempotent and fires every
   2 s; never run the in-process and dedicated tickers concurrently. Use a Redis
   **ticker-owner lease** (or stop web ticking *before* starting the external one).

Foundations safe to build behind off-flags meanwhile: `RedisPresenceStore`
(`PRESENCE_REDIS_ENABLED`, in-memory fallback — note the live-socket-must-not-expire
TTL + `sid→owner` reverse-map gotchas in the 1B plan) and Socket.IO
`message_queue=REDIS_URL` (`SOCKETIO_REDIS_MQ_ENABLED`) — keep the in-process ticker
as owner while validating.

### Stage 2 — more concurrency: multiple single-worker *containers* (NOT `-w 2`)
`gunicorn -w 2` is unusable here: Flask-SocketIO under `GeventWebSocketWorker`
cannot run multiple workers under one master (no sticky routing between them). To
add foreground capacity, run **N separate backend *containers*, each `gunicorn -w 1`**,
behind **Caddy sticky routing** (cookie/IP) + the Redis `message_queue` (so emits
cross processes). Prerequisite: audit process-local state first
(`game_state_service.games`/`game_locks`, the preflop-leak cache, boot/watchdog
jobs) — sticky sessions keep a live game on one container but do NOT protect shared
SQLite rows from concurrent writers across containers. Each container pays the
~550 MB baseline.

### Stage 3 — bigger box (best ROI)
Hetzner CPX21 (4 GB, ~+€6/mo) gives RAM/CPU headroom on the single box. Pure ops
change (`docker-compose.prod.yml` + Hetzner resize). Combined with Stage 0 tuning,
this is the cheapest route to comfortable 20-user headroom.

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

## Near-term recommendation (the 20-user target)

- **Stage 1A** ✅ done.
- **Stage 0 tuning is the actual 20-user fix** — keep `-w 1`: lower
  `DECISION_ANALYSIS_ITERATIONS` (~250), validate `SOCKETIO_ASYNC_MODE=gevent`, ease
  ticker pacing, templated off-screen narration. Cheap, low-risk, reversible per knob.
- **Stage 3** (4 GB box) if tuning alone leaves it tight — best ROI.
- **1B / 2 are NOT needed for 20 users** and carry real correctness work (the 1B
  prerequisites above + the no-`-w 2` constraint). Defer until a single *tuned*
  `-w 1` box is genuinely exhausted.
- The `RedisPresenceStore` + Socket.IO MQ foundations can be built behind off-flags
  any time (zero prod risk), but they don't add capacity until 1B/2 land.

## What NOT to do

- **Don't share the world** — it breaks the casual design and isn't needed (the
  per-user sim is ~free and self-throttling).
- **Don't jump to Postgres early** — exhaust Stage 3 first; the migration surface
  is large and SQLite handles current load comfortably.
- **Don't run `gunicorn -w 2`** — Flask-SocketIO + `GeventWebSocketWorker` can't
  multi-worker under one master (no inter-worker sticky routing). Add capacity with
  separate single-worker *containers* behind a sticky LB + Redis MQ instead.
- **Don't add backend containers without RAM** — each pays the ~550 MB baseline; two
  on the current 1.9 GB box exceeds the budget.
- **Don't extract the ticker before its prerequisites** (decouple from web-worker
  game memory + cross-process seat lock + ticker-owner lease) — it mutates live
  tables and will corrupt seats/ledgers across processes otherwise.
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
