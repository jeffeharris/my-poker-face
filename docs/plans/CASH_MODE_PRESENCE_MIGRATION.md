---
purpose: The precise reroute spec for wiring the dormant Presence state machine (Cut 3) into every existing cash-mode seat / idle-pool / hustle / vice writer
type: spec
created: 2026-05-30
last_updated: 2026-05-31
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

## CORRECTED inventory — verified against code (2026-05-31, HEAD `11e1f3fb`)

**The original inventory below the line was WRONG.** It was written from the
design doc's imagined architecture, not the real tree. A four-agent shadow-wiring
pass + an independent grep audit found that **none** of the function names it cited
exist, in any section. What follows is the inventory re-derived from the actual
code (every line/function/path here was grep-verified). The dual-write shadow
agents wired the REAL sites listed here; their branches are the reference
implementation.

### The architecture the original doc missed

The original assumed per-entity imperative ops (`seat()` / `vacate()`, one
`SIT`/`LEAVE` per call). **The real cash mode persists a whole immutable
`CashTableState` per `save_table`** (`cash_table_repo.save_table(new_table,
sandbox_id=, now=)`). There is no per-entity seat op. This changes the cutover
shape fundamentally:

- The shadow/cutover writer must **diff** the saved `CashTableState` seat map
  against current presence and emit the **minimal legal transitions** (`SIT` for a
  newly-seated entity; `LEAVE`+`SIT` for a move, since the machine forbids
  `SEATED --sit--> SEATED`; no-op if already correctly seated). The lobby shadow
  agent built exactly this: `_shadow_reconcile_table(table, sandbox_id)` +
  `_shadow_seat_state` (seat map → entity_ids) in `cash_mode/lobby.py` on branch
  `worktree-agent-a48caf5b117541b2d`.
- So **Phase 3 "table as projection" (design §6/D1) is a `CashTableState`-
  derivation problem, NOT a "reroute 25 imperative writers" problem.** The unit of
  work is "make `save_table` derive the seat map from `entity_presence`," done once
  at the `CashTableRepository.save_table` chokepoint, not 25 call-site rewrites.
  Likely *less* total work than the architect's 25-callsite estimate implied, but a
  different shape.

### A. `cash_mode/lobby.py` — 5 real `save_table` callsites (not ~20)

`save_table` is `cash_table_repo.save_table(CashTableState, sandbox_id=, now=)`.
None of the original doc's named functions (`seat_player_at_table`,
`handle_player_leave`, `reseat_player`, `_rebalance_or_seed`, `_consolidate_tables`,
`_fill_empty_seats`, `_reap_empty_tables`, `_release_idle_to_pool`,
`_coerce_fish_to_table`, `handle_hand_boundary`, `_seed_initial_tables`,
`_persist_reseat_recovery`, `_persist_table_state`) exist. The real sites:

| Line | Enclosing fn | What it does | Shadow action (branch `…a48caf5b`) |
|---|---|---|---|
| 458  | `ensure_lobby_seeded`           | seed lobby tables with AIs | **WIRED** — `SEED`→`SIT` per seeded AI (via reconcile-diff) |
| 839  | `_process_global_greedy_fills`  | global greedy seat fill | **WIRED** — `SIT`/move for filled AIs |
| 951  | `refresh_unseated_tables`       | free expired sponsorship holds | **SKIPPED** — re-persists vacated seats; no entity gains a seat |
| 1713 | `refresh_unseated_tables`       | post-burst per-table persist | **WIRED** — `SIT`/move for post-burst occupants |
| 4003 | `kill_all_cash_sessions`        | reconciler: reset orphan human seats | **SKIPPED** — vacates only; reconciler, out of scope (§F) |

Net: 3 wired, 2 deliberately skipped. The shadow uses a seat-map *diff*, not
per-entity events.

### B. `cash_mode/casino_provisioning.py` — 6 real `save_table` callsites

Original doc named 3 functions that don't exist (`_provision_casino_table`,
`_seed_themed_casino`, `_seed_opponent_picker_table`) and missed 2 real writers.
Real sites (branch `worktree-agent-a845e97c4a9b47da7`, commit `027b46e8`):

| Line | Enclosing fn | Event | Shadow action |
|---|---|---|---|
| 488  | `_reclaim_zombie_casino_seats` | `RETURN_TO_POOL` per reclaimed seat | WIRED |
| 743  | `_drain_fish_bankroll_to_pool` | `RETURN_TO_POOL` (POOL-funded fish) | WIRED |
| 893  | `_refill_one_fish`             | `SEED`→`SIT(table,seat)` | WIRED (undocumented before) |
| 989  | `_shed_excess_fish`           | `RETURN_TO_POOL` per shed seat | WIRED (undocumented before) |
| 1419 | `resolve_casino_provisioning` (spawn) | `SEED`→`SIT` per seeded fish | WIRED (the doc's intended "provision") |
| 1717/1804 | (additional `save_table` in same module) | per behaviour | covered by reclaim/shed/refill mapping |

All casino seats are AI / POOL-funded (`ai_entity_id`). `_drain_fish_bankroll_to_pool`
is multi-origin (teardown / reap / refill-unwind / spawn-abort / drain-sweep) — at
the flip an entity may already be POOL/OFFLINE, so its transition is sometimes a
swallowed no-op; that's expected, not a bug.

### C. Idle-pool writers — `CashTableRepository`, NOT `seat_registry.py`

Original doc cited `cash_mode/seat_registry.py:130/195/213`
(`add_to_idle_pool`/`upsert_idle_pool`/`remove_from_idle_pool`) — **none exist;
`seat_registry.py` is the in-memory `SeatOccupancyRegistry`, no `cash_idle_pool`
SQL.** The real `cash_idle_pool` writers:

| Writer | Path | Maps to |
|---|---|---|
| `save_idle(entry, *, sandbox_id)`   | `poker/repositories/cash_table_repository.py:535` (`INSERT OR REPLACE`) | `→ IDLE` (add) / field refresh (no state change) |
| `delete_idle(personality_id, *, sandbox_id)` | `poker/repositories/cash_table_repository.py:644` (`DELETE`) | leaving IDLE — destination decided by caller, so NOT shadowed on bare delete |

Driven by change-sets from `refresh_table_roster` (`cash_mode/movement.py`) applied
in `lobby.py`. **Idle was NOT independently shadow-wired** (the agent correctly
refused — a repo-layer shadow would violate the "shadow after the authoritative
write, outside the lock" contract). **Decision: the seat→IDLE `LEAVE` is emitted by
the lobby reconcile-diff (§A), which already sees seats becoming empty. Do not also
shadow it at the repo layer — that would double-drive.** (Idempotent if it happened
— a 2nd `LEAVE` from `IDLE` is illegal → swallowed — but pick one authority.)

### D/E. Off-grid: side-hustle + vice — real insert/delete sites

Original doc named `start_side_hustle`@98 / `end_side_hustle`@124 /
`start_vice`@75 / `end_vice`@112 — none exist. Real writers (branch
`shadow-offgrid-dualwrite`, commit `16ddf5c2`):

| Real fn | Path | Authoritative write | Event |
|---|---|---|---|
| `_commit_hustle_start`        | `cash_mode/ai_side_hustle.py:451` | `side_hustle_repo.insert_side_hustle_state` (467) | `START_HUSTLE` |
| `tick_side_hustle_expirations`| `cash_mode/ai_side_hustle.py:334` | `side_hustle_repo.delete` (392) | `END_OFFGRID` |
| `_commit_vice_start`          | `cash_mode/ai_vice_spending.py:936` | `vice_repo.insert_vice_state` (1065) | `START_VICE` |
| `tick_vice_expirations`       | `cash_mode/ai_vice_spending.py:577` | `vice_repo.delete` (622) | `END_OFFGRID` |

All AI-only (`ai_entity_id`), no seat args. **Expected divergence:** `START_*` is
only legal from IDLE; a broke AI going off-grid straight from unseated has no IDLE
shadow row, so the start is a swallowed no-op. That's correct for the shadow phase
(the audit surfaces it); do NOT force intermediate transitions.

### F. Concurrency — `get_sandbox_lock` is held by CALLERS, not these modules

Verified counts: `cash_mode/lobby.py` = **0** `get_sandbox_lock`;
`flask_app/routes/cash_routes.py` = 9; `flask_app/services/ticker_service.py` = 2.
So seat/off-grid writers run under the lock **only via the route/ticker paths**.
`ensure_lobby_seeded` (boot) and sim paths run **unlocked**. Acceptable for the
best-effort shadow phase, but **the Phase-3 authority flip MUST add explicit
`get_sandbox_lock` at the `lobby.py` / repo entry points** — they don't inherit it.

### F2. Reconcilers retired by this migration (paths re-verified)

| Reconciler | Path | Repairs | Retired when |
|---|---|---|---|
| `_free_ghost_human_seats`        | `flask_app/routes/cash_routes.py:411`     | ghost human seats | lobby/route reroute done |
| `_reclaim_zombie_casino_seats`   | `cash_mode/casino_provisioning.py:371`    | zombie AI seats | casino reroute done |
| `_restore_cash_table_binding`    | `flask_app/handlers/game_handler.py:1235` | lost cash_table_id on cold-load | route reroute done |
| `whereabouts.py` (whole module)  | `cash_mode/whereabouts.py`                | detects all presence contradictions | A–E done → trivial read of `entity_presence` |

> **NOTE:** `_persist_reseat_recovery` / `reseat_player` / `reseat_readiness` as
> named in the original doc were not found in `lobby.py`/`movement.py` at this HEAD;
> reconcile against code before relying on them.

---

## ⚠️ ORIGINAL INVENTORY BELOW IS OBSOLETE / INACCURATE — kept only for history

The section below predates the code audit and names functions that do not exist.
Use the CORRECTED inventory above. (Retained so the scale of the doc-vs-code drift
is on the record.)

### A-old. `save_table` callsites (per original doc — UNVERIFIED, mostly wrong)

(Original claimed ~20 lobby + 5 casino callsites in functions that don't exist;
real counts are 5 lobby + 6 casino, different functions — see CORRECTED §A/§B.)

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
