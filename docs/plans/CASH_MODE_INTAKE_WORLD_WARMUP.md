---
purpose: Plan to pre-warm a new player's hidden world with a deterministic background simulation burst during the Scene-0 session, so the lobby feels lived-in by graduation
type: spec
created: 2026-06-07
last_updated: 2026-06-07
---

# Intake world warm-up — pre-warm the world during Scene 0

## Goal

A brand-new player's world is **seeded and already ticking** during intake/Scene 0
(`ensure_lobby_seeded` creates all hidden cardrooms; the world ticker plays
off-screen hands at unseated tables for the active sandbox — see
`flask_app/routes/cash_routes.py:5678,5904` and `flask_app/services/ticker_service.py`).
But it starts from **zero history exactly when the player arrives** and accrues
only ~1 hand/table per ~2s. So at graduation the world has no depth: relationships
are fresh, the economy hasn't moved, AIs haven't redistributed.

**This plan adds a one-shot warm-up BURST** that runs a chunk of deterministic,
no-LLM simulation across the hidden cardrooms while the player plays Scene 0, so
the lobby they graduate into feels **lived-in** — regulars who already know each
other, an economy in motion, AIs spread across tables with histories.

## Decisions (locked with the user, 2026-06-07)

- **New users go through the Circuit intro — no skip (v1).** It *is* the
  onboarding; skipping it leaves `career_active` off → the old full-lobby product.
  The `classify_new_player` "grandfather" path already spares existing sandboxes.
- **No separate sign-in/auth hook.** "Intake at sign-in" is satisfied by the
  existing first-lobby landing (seconds after sign-in). The warm-up's value comes
  from the burst, not a few seconds of extra head-start; touching the auth path
  isn't worth it.
- **Trigger: the Scene-0 sit** (intake submit → auto-sit = "the first table
  sequence begins"). The burst then runs in the background for the whole session.
- **Intensity: moderate ("reads form").** ~30 hands/table so the per-sandbox
  `AIMemoryManager` accumulates real opponent reads and the economy shifts.
  Tunable constant; start at 30 and adjust from playtest.
- **Fidelity: cheap deterministic, no LLM.** Reuse the offline-sim path (real
  cardplay + relationship/economy evolution, fake vice, no chat narration). Fast
  (~4ms/hand) and zero API cost — nobody reads warm-up action.
- **What warms: AI↔AI only** (relationships, economy, whereabouts). The human
  isn't in those hands, so no AI→human regard moves — consistent with the
  social-accrual vouch decision (`CASH_MODE_CAREER_M2_PLAN.md`).

## Build

### 1. Warm-up function — `cash_mode/` (near `sim_runner.py` / `lobby.py`)
`warm_up_world(sandbox_id, repos, *, hands_per_table=WARMUP_HANDS_PER_TABLE,
max_hands=WARMUP_MAX_HANDS, max_seconds=WARMUP_MAX_SECONDS) -> WarmupResult`
- Plays deterministic, no-LLM hands across **unseated** tables (the pinned/seated
  Scene-0 table is skipped automatically by `refresh_unseated_tables`).
- Reuses the per-sandbox cached `AIMemoryManager` so reads accumulate (that IS
  "moderate / reads form").
- **Chunked + bounded:** play K hands, then `socketio.sleep(0)` to yield; stop at
  the hand-count or wall-clock cap (a backstop so a slow box can't wedge it).
- **Conservation:** goes through the same chip-conserving `refresh_unseated_tables`
  / `full_sim` path as the offline sim — no mint; ledger audit stays flat.
- Returns a small result (tables touched, hands played, elapsed) for logging.

### 2. Constants — `cash_mode/economy_flags.py` (or a warm-up module)
- `WARMUP_HANDS_PER_TABLE = 30`, `WARMUP_MAX_HANDS = 300`, `WARMUP_MAX_SECONDS = 5`.
- `INTAKE_WORLD_WARMUP_ENABLED` flag (default ON; lets us kill it instantly).

### 3. One-shot guard — `CareerProgress`
- Add `world_warmed: bool = False` (small schema bump on `career_progress`;
  `to_json` / `from_row`). Set True after the burst so it never re-runs on
  reconnect/refresh. Belt-and-suspenders with an in-process per-sandbox guard set.

### 4. Trigger wiring — the Scene-0 sit path (`flask_app/routes/cash_routes.py`)
- After a brand-new career player sits into the Scene-0 table (the auto-sit after
  intake), if `INTAKE_WORLD_WARMUP_ENABLED` and not `progress.world_warmed`:
  launch `socketio.start_background_task(warm_up_world, ...)` and flip the guard.
- Background task acquires `game_state_service.get_sandbox_lock(sandbox_id)` in its
  chunks so it interleaves cleanly with the live ticker (no double-play).
- Best-effort: any failure is logged and swallowed — warm-up must never break the
  sit / Scene-0 flow.

### 5. (Optional, small) Eager seed+activate at first landing
- The first `GET /api/cash/lobby` already seeds + `presence.touch()`s. Confirm a
  brand-new user's sandbox is active from that first call so the ticker is already
  running by the time the burst kicks (no new auth hook needed).

## Tests
- **One-shot guard:** burst runs once; a second sit / lobby reload is a no-op
  (`world_warmed` honored).
- **Conservation:** total chips before == after the burst (ledger audit flat) —
  the soft spot; assert no mint.
- **Skips the Scene-0 table:** the pinned/seated table plays no warm-up hands.
- **Bounded:** respects `WARMUP_MAX_HANDS` / `WARMUP_MAX_SECONDS`.
- **Reads form:** after a burst, opponent models for warmed tables have
  observations (sanity that "moderate" actually populates reads).
- **Flag off:** `INTAKE_WORLD_WARMUP_ENABLED=False` → no burst, no guard write.

## Risks / watch-items
- **Lock contention** with the ticker → mitigated by chunked play + yields; cap
  the per-chunk hand count so the lock is never held long.
- **Conservation** is the known soft spot (mirrors the M3 freeroll worry) — route
  exclusively through the audited sim path; the conservation test gates it.
- **Budget creep** — keep intensity a constant; "moderate" is a starting guess.
- **Determinism** — seed the warm-up RNG from the sandbox so reruns/tests are
  reproducible (and to honor the functional-core "no global RNG" rule).

## File pointers
- Seeding + lobby filter + intake/Scene-0 trigger: `flask_app/routes/cash_routes.py`
  (`ensure_lobby_seeded` ~5678, `visible_tables` ~5908, intake route ~6475,
  auto-sit/Scene-0 sit path).
- World runtime to reuse: `cash_mode/lobby.py` (`refresh_unseated_tables`),
  `cash_mode/full_sim.py` (`play_one_hand`), `cash_mode/sim_runner.py`
  (per-sandbox memory cache pattern).
- Per-sandbox lock: `flask_app/services/game_state_service.get_sandbox_lock`.
- Background task + socket: `flask_app/services/ticker_service.py` (pattern for
  `socketio.start_background_task` + cooperative yields).
- Career state + guard: `poker/repositories/career_progress_repository.py`.
