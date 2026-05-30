---
purpose: The precise reroute spec for wiring the dormant Presence state machine (Cut 3) into every existing cash-mode seat / idle-pool / hustle / vice writer
type: spec
created: 2026-05-30
last_updated: 2026-05-30
---

# Cash Mode — Presence Machine Migration Spec

This document is the spec for the **next (human-reviewed) phase** of the
state-model work. The Presence state machine and its backing table already exist
and are **dormant** (built in Cut 3, see below); this doc enumerates every
current writer that a later phase must reroute through the machine, so that
`entity_presence` becomes the single authority for "where is this actor?" and the
`seated_and_idle` / `double_seat` / `seated_and_offgrid` / `stale_idle` bug
classes become unrepresentable.

It is the companion to the design doc `CASH_MODE_STATE_MODEL.md` (read that first
— §5.1 is the machine, §6 the table-as-projection decision, §6.1 the lock
contract). This doc is the *callsite-level* inventory the design doc deferred.

## What already shipped (Cut 3 — additive & dormant)

These exist now and change **no behaviour** (nothing reads or writes them on a
live path yet):

| Artifact | Path | Role |
|---|---|---|
| Pure machine | `cash_mode/presence.py` | Frozen `PresenceState`, `PresenceEvent`, `LEGAL_TRANSITIONS`, `transition()` |
| Table | `entity_presence` (schema **v128**, `poker/repositories/schema_manager.py`) | One authoritative row per `(entity_id, sandbox_id)` |
| Repository | `poker/repositories/entity_presence_repository.py` | `load` / `save` / `persist_transition` / `seat_occupant` / `list_for_sandbox` |
| Tests | `tests/test_cash_mode/test_presence_machine.py` | Pure-machine + repo (temp SQLite, no app) |

The table's structural guards:

- **Compound PK `(entity_id, sandbox_id)`** → exactly one state per entity per
  sandbox → `seated_and_idle` cannot be stored.
- **Partial unique index** `(sandbox_id, table_id, seat_index) WHERE state='seated'`
  → two entities cannot hold one seat → `double_seat` cannot be stored.
- **CHECK constraints** → `seated` ⇔ has `(table_id, seat_index)`; every other
  state has neither → no ghost-seat / orphaned-seat rows.

`entity_id` uses the ledger convention: `player:<owner_id>` / `ai:<personality_id>`
(plus pool-funded casino AI carry a `pool` origin state, design §6.2).

## Concurrency contract (non-negotiable, design §6.1)

The machine and repository are **pure / lock-free**. Every reroute below MUST run
the read → `persist_transition` → (chip-custody + session writes) cycle **inside
one `get_sandbox_lock(sandbox_id)` critical section** (`flask_app/services/game_state_service.py:177`).
The DB constraints are a last-line backstop, not a substitute for the lock. A
half-migrated state (two seat writers — old `seat_map` + new `entity_presence`)
is itself the bug, so the seat-map demotion (design Phase 3) must land atomically,
not callsite-by-callsite.

## Correction to the design doc (verified against `development`, 2026-05-30)

The design doc (§7 Phase 3, §8) states ~30 `save_table` callsites across **four**
modules including `flask_app/routes/cash_routes.py` and
`flask_app/handlers/game_handler.py`. A source audit on `development` (HEAD
`adc9f2c1`, schema v127) found:

- `save_table` is defined once (`cash_mode/tables.py:54`) and called **only** in
  **two** modules: `cash_mode/lobby.py` (20 callsites) and
  `cash_mode/casino_provisioning.py` (5 callsites) — **25 callsites**, not ~30.
- `flask_app/routes/cash_routes.py` and `flask_app/handlers/game_handler.py`
  contain **zero** direct `save_table` calls. They mutate seats *indirectly*
  through registry methods and by reading/writing `seat_map` (cash_routes:
  ~11 `seat_map` refs, ~16 `table_registry.*` refs; game_handler: ~7 `seat_map`,
  ~6 registry refs). Those are real seat-write paths that Phase 3 must still
  route through Presence, but they are not `save_table` callers — the design
  doc's attribution was imprecise.

Net: the reroute surface is the same *shape* (sit / leave / reseat / provisioning
/ hand-boundary), but the `save_table` count is 25 and the route/handler seat
writes go through the registry, not `save_table` directly.

---

## Inventory — every writer to reroute

### A. `save_table` callsites (seat-map writers) — the §6/Phase 3 demotion

`save_table(table_id, seat_map, sandbox_id, stake_level)` (`cash_mode/tables.py:54`)
writes the occupancy half of `cash_tables`. Under table-as-projection (design D1)
the seat map becomes a *read model* of Presence ∩ Chip-custody; each of these
writers must instead drive `entity_presence` transitions, and the seat map must be
derived, never written independently.

#### `cash_mode/lobby.py` (20 callsites)

| Line | Enclosing fn | What it does | Presence event(s) |
|---|---|---|---|
| 185  | `_seed_initial_tables`     | seed sandbox's first tables w/ AIs | `SEED`→`SIT` per AI |
| 574  | `_persist_reseat_recovery` | persist idle→seat recovery (reconciler) | `RESEAT` |
| 606  | `reseat_player`            | idle pool → seat re-entry | `RESEAT` |
| 733  | `handle_player_leave`      | player/AI leaves a seat | `LEAVE` (or `RETURN_TO_POOL` for pool AI) |
| 902  | `_fill_empty_seats`        | fill open seats from eligible pool | `SIT`/`RESEAT` |
| 917  | `_fill_empty_seats`        | (second write in same fn) | `SIT`/`RESEAT` |
| 1015 | `seat_player_at_table`     | primary sit path | `SIT` |
| 1058 | `seat_player_at_table`     | (second write in same fn) | `SIT` |
| 1124 | `_rebalance_or_seed`       | rebalance field across tables | `LEAVE`+`SIT` (move) |
| 1126 | `_rebalance_or_seed`       | (second write) | `LEAVE`+`SIT` |
| 1158 | `_consolidate_tables`      | merge short tables | `LEAVE`+`SIT` (move) |
| 1162 | `_consolidate_tables`      | (second write) | `LEAVE`+`SIT` |
| 1194 | `_release_idle_to_pool`    | seat → idle pool | `LEAVE` |
| 1207 | `_reap_empty_tables`       | tear down empty table | (none — table teardown is static config) |
| 1212 | `_reap_empty_tables`       | (second write) | (none) |
| 1217 | `_reap_empty_tables`       | (third write) | (none) |
| 1262 | `_persist_table_state`     | generic seat-map persist helper | derive from Presence |
| 1769 | `_coerce_fish_to_table`    | force a fish into a seat | `SIT` (pool AI) |
| 2090 | `handle_hand_boundary`     | per-hand seat reconcile | derive from Presence |
| 2117 | `handle_hand_boundary`     | (second write) | derive from Presence |

#### `cash_mode/casino_provisioning.py` (5 callsites)

| Line | Enclosing fn | What it does | Presence event(s) |
|---|---|---|---|
| 371  | `_reclaim_zombie_casino_seats` | reconciler: free zombie AI seats | `RETURN_TO_POOL` / delete |
| 697  | `_drain_fish_bankroll_to_pool` | fish chips→pool on removal | `GO_OFFLINE`/`RETURN_TO_POOL` |
| 739  | `_provision_casino_table`      | build a casino table + seat AIs | `SEED`→`SIT` per AI |
| 967  | `_seed_themed_casino`          | seed a themed roster | `SEED`→`SIT` per AI |
| 1029 | `_seed_opponent_picker_table`  | seed the picker table | `SEED`→`SIT` per AI |

### B. Route / handler seat writers (indirect — through the registry / seat_map)

These do not call `save_table` directly but read-modify-write `seat_map` and/or
call `table_registry.*`; Phase 3 must route them through Presence too.

| File | Surface | Notes |
|---|---|---|
| `flask_app/routes/cash_routes.py` | ~11 `seat_map` refs, ~16 `table_registry.*` refs | sit / leave / reseat / solo-reseat endpoints; `_free_ghost_human_seats` reconciler at `:411` |
| `flask_app/handlers/game_handler.py` | ~7 `seat_map` refs, ~6 registry refs | hand-boundary cash sync; `_restore_cash_table_binding` cold-load reconciler at `:1235` |

### C. Idle-pool writers — `cash_idle_pool` becomes a Presence projection

`cash_idle_pool` (PK `(personality_id, sandbox_id)`) is today an independent
authority that can disagree with the seat map (the source of `seated_and_idle`).
Under the machine, IDLE is a presence *state*; the pool is a read-model.

| Writer | Path | Maps to |
|---|---|---|
| `add_to_idle_pool`      | `cash_mode/seat_registry.py:130` (INSERT `cash_idle_pool`) | `→ IDLE` |
| `upsert_idle_pool`      | `cash_mode/seat_registry.py:195` (INSERT/UPSERT)           | IDLE field update (energy/buy_in) — keep as projection detail, not a state change |
| `remove_from_idle_pool` | `cash_mode/seat_registry.py:213` (DELETE)                  | leaving IDLE (`SIT`/`RESEAT`/`START_*`/`GO_OFFLINE`) |
| `reseat_readiness`      | `cash_mode/movement.py:264`                                | read of IDLE recovery — becomes a read of Presence + projection fields |

### D. Side-hustle writers — `ai_side_hustle_state` becomes a projection

| Writer | Path | Maps to |
|---|---|---|
| `start_side_hustle` | `cash_mode/ai_side_hustle.py:98`  | `IDLE → SIDE_HUSTLE` |
| `end_side_hustle`   | `cash_mode/ai_side_hustle.py:124` | `SIDE_HUSTLE → IDLE` (timer-driven `END_OFFGRID`) |

### E. Vice writers — `ai_vice_state` becomes a projection

| Writer | Path | Maps to |
|---|---|---|
| `start_vice` | `cash_mode/ai_vice_spending.py:75`  | `IDLE → VICE` |
| `end_vice`   | `cash_mode/ai_vice_spending.py:112` | `VICE → IDLE` (timer-driven `END_OFFGRID`) |

### F. Reconcilers retired by this migration

Each becomes unnecessary (or degrades to a trivial read) once the above are
rerouted and the contradiction it repairs is unrepresentable. (Cross-referenced
with `CASH_MODE_STATE_MODEL.md` §8.) **Do not edit the reaper
(`_boot_sweep_stale_cash_rows`) under this track** — a separate track owns it.

| Reconciler | Path | Repairs | Retired when |
|---|---|---|---|
| `_free_ghost_human_seats`        | `flask_app/routes/cash_routes.py:411`   | ghost human seats | B rerouted |
| `_reclaim_zombie_casino_seats`   | `cash_mode/casino_provisioning.py:371`  | zombie AI seats | A.casino rerouted |
| `_restore_cash_table_binding`    | `flask_app/handlers/game_handler.py:1235` | lost cash_table_id on cold-load | B rerouted |
| `_persist_reseat_recovery`       | `cash_mode/lobby.py:574`                | idle→seat re-entry | A.lobby rerouted |
| `reseat_readiness`               | `cash_mode/movement.py:264`             | idle→seat readiness | C rerouted |
| `whereabouts.py` (whole module)  | `cash_mode/whereabouts.py`              | detects all presence contradictions | A–E done → degrades to a trivial read of `entity_presence` |

## Sequencing (within the human-reviewed phase)

1. **Dual-write shadow (safe, reversible).** Add Presence transitions *alongside*
   the existing writers (still authoritative), guarded by an `economy_flags`
   kill switch. Compare `entity_presence` against `cash_tables` / `cash_idle_pool`
   in a read-only audit (reuse `whereabouts.py`) to prove zero divergence on live
   traffic before flipping authority.
2. **Flip authority (the atomic Phase-3 cut).** In one change: make `seat_map` /
   `cash_idle_pool` / `ai_*_state` *projections* derived from `entity_presence`;
   reroute A–E to write Presence only; hold the sandbox lock across each. This is
   NOT independently shippable callsite-by-callsite (two writers = the bug).
3. **Retire reconcilers (F).** Delete each as its class becomes unrepresentable.

## Cutover gotchas (found in review, 2026-05-30)

- **`entity_presence.sandbox_id` has `DEFAULT 'default'`** but the rest of cash
  mode keys on real sandbox UUIDs. Harmless while dormant, but every reroute MUST
  pass an explicit `sandbox_id` — a write that falls back to the `'default'`
  bucket would silently collide entities from different save-files under one key.
  Consider dropping the column default during the cutover so an omitted
  `sandbox_id` fails loud instead of silently mis-bucketing.
- **Name overlap:** `cash_mode/presence.py` (this seat/idle state machine) vs
  `flask_app/services/presence.py` (Socket.IO *connection* presence — the world
  ticker's "is this user online" tracker). They are unrelated; do not merge or
  cross-import. The cutover engineer will touch both worlds (a human going offline
  is a connection-presence event that may drive a `GO_OFFLINE` seat transition),
  so keep the distinction explicit.
- **`updated_at` is caller-supplied / opaque** in the pure machine (the DB column
  defaults to `CURRENT_TIMESTAMP` when None). Pick one stamping policy at cutover
  (DB clock vs app clock) and apply it uniformly, or history ordering across
  reroutes will be inconsistent.

## Out of scope for this migration

- The reaper / chip-settling (`_boot_sweep_stale_cash_rows`) — separate track.
- The chip-custody machine, the unified human+AI ledger, `player_bankroll_state`
  — those are Cuts 1–2 / Phases 0–1 of the design, owned elsewhere.
- `full_sim.py` hot-path batching (design §6.2) — sims must drive Presence for AI
  through the same machine but must not pay per-transition SQLite cost on the
  ticker hot path; batch or in-memory-then-flush. Spec'd here as a constraint, not
  built.
- Career/scripted seeding that writes `cash_tables` directly (the
  `circuit-progression` work, design §6.2) — inventory it before the Phase-3 cut.
