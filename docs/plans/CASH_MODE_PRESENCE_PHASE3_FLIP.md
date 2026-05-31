---
purpose: Implementation blueprint for Phase 3 of the cash-mode Presence cutover — making entity_presence the authoritative store for actor location, demoting cash_tables seat map / cash_idle_pool / ai_*_state to projections
type: design
created: 2026-05-31
last_updated: 2026-05-31
---

# Cash Mode — Presence Phase-3 Authority Flip (design pass)

This is the reviewed-before-execution blueprint for the **irreversible** step of
the Presence cutover: make `entity_presence` the single authority for "where is
each actor," so `seated_and_idle` / ghost-seat / double-seat become structurally
impossible. Produced by a `feature-dev` code-explorer (territory map) +
code-architect (blueprint) pass, with refinements noted inline.

**Read first:** `docs/plans/CASH_MODE_STATE_MODEL.md` (design),
`docs/plans/CASH_MODE_PRESENCE_MIGRATION.md` (CORRECTED callsite inventory),
`docs/plans/CASH_PRESENCE_CUTOVER_HANDOFF.md` (state + Steps 1/2 done).

## Status of the precondition (accurate)

Phase 1 (shadow) + Phase 2 (§C dedup) are merged and **validated in SIMS only**
(`scripts/validate_presence_shadow.py`, 0 unexpected divergence, AI + human
paths). The shadow has **NOT** yet been run on live dev traffic. Two pre-flip
gates remain open and recommended:
- (optional but advised) flip `PRESENCE_SHADOW_WRITE_ENABLED` on the dev server,
  let real traffic run, audit divergence on live data;
- confirm the `circuit-progression` (career/scripted seeding) branch — which
  writes `cash_tables` directly — routes through `save_table` (so it inherits
  presence tracking) before it merges to `development`. (It is currently a
  separate unmerged branch; not in scope for the flip, but a merge-time gate.)

## The core mechanism: diff-at-the-`save_table`-chokepoint

`CashTableRepository.save_table(state, *, sandbox_id, now)`
(`poker/repositories/cash_table_repository.py:122`) is a near-universal
chokepoint for seat writes — nearly every production seat write goes through it,
it already loads the prior row (~line 153) and atomically clears the idle pool in
ONE sqlite connection. Phase 3 promotes the proven shadow reconcile-diff
(`cash_mode/lobby.py:_shadow_reconcile_table`) into an **authoritative** write
that runs **inside `save_table`'s connection/transaction**, so
`entity_presence` + `cash_tables` + idle commit together (no cross-connection
desync window).

A new module **`cash_mode/presence_transitions.py`** exposes
`emit_presence_transitions_for_save(conn, sandbox_id, old_seats_blob, new_table,
now_iso, idle_metadata=None)`. Given the already-open connection it:
1. reads current `entity_presence` SEATED rows for this table (same txn);
2. **origin-derives** the event for each departed seat from the slot kind:
   `kind=='human'` → `GO_OFFLINE` (a human cashes OUT of the sandbox; IDLE is the
   AI idle-pool concept — design §5.1); `archetype=='fish'`/whale → `RETURN_TO_POOL`;
   else AI → `LEAVE` (→IDLE); `kind=='reserved'` → ignored (no presence state);
3. emits `SIT` (or `SEED`→`SIT` for a fresh fish/whale; `LEAVE`-then-`SIT` for a
   cross-table move) for each newly-occupied seat;
4. writes `entity_presence` (+ the idle satellite) via direct SQL on `conn`.

Gated on a NEW flag `PRESENCE_AUTHORITY_ENABLED` (default False) — distinct from
`PRESENCE_SHADOW_WRITE_ENABLED`. Off ⇒ literal no-op (zero behavior change).

## Key decisions

### D1 — idle metadata: satellite table, NOT presence columns
`IdlePoolEntry` carries `reason` (forced_leave / stake_up_queued / take_break /
bored_move) and `target_stake`, used by the idle-candidate filter
(`movement.py:~1400`). `entity_presence` has no columns for these and shouldn't —
they're meaningless for non-IDLE states and would pollute the pure machine. New
**`cash_idle_metadata(personality_id, sandbox_id, reason, target_stake,
left_at)`** (schema v129) carries the routing payload; `entity_presence` owns the
STATE. The reason can't be derived from the seat diff alone, so
`emit_presence_transitions_for_save` takes an optional `idle_metadata` dict
(keyed by personality_id) that lobby callers fill from `result.idle_changes`;
unknown callers (casino reclaim, kill_all) default to `forced_leave`.

### D2 — `cash_idle_pool` stays a WRITTEN CACHE first; VIEW conversion DEFERRED
**(Refinement of the architect's blueprint.)** The architect proposed converting
`cash_idle_pool` to a SQL view in Step 0, which breaks every `save_idle`/
`delete_idle` writer at once (HIGH blast radius, their Risk 1). That is
inconsistent with their own treatment of `cash_tables.seats_json` (kept as a
written cache, view-demotion deferred to a later step). **Treat idle the same
way:** presence is authoritative; `cash_idle_pool` keeps being WRITTEN as a
presence-derived cache during the transition, and the hard view-conversion is a
separate, later, well-audited step — not part of the irreversible cut. This
keeps Step 0 small and reversible.

### D3 — atomicity: presence shares `save_table`'s connection
`emit_presence_transitions_for_save` writes via the connection `save_table`
already holds, so presence + seats commit in one sqlite transaction. A double-seat
attempt raises `IntegrityError` (partial-unique index) → the whole `save_table`
rolls back (loud, not silent). Chip-custody/ledger atomicity is OUT OF SCOPE
(separate machine / Cuts 1-2).

### D4 — repo injection: share the db_path connection (no new wiring)
All repos share one sqlite `db_path`; `emit_presence_transitions_for_save`
receives `save_table`'s open connection, so it needs no separate
`EntityPresenceRepository` and works identically in Flask and sim. Off-grid
transitions (which don't go through `save_table`) still use
`EntityPresenceRepository.persist_transition`; the sim wires
`flask_app.extensions.entity_presence_repo` from its `repos` dict (the pattern
`validate_presence_shadow.py` already uses).

### D5 — locking: caller-side only, add explicit locks at the unlocked entries
`get_sandbox_lock` (`game_state_service.py:177`) is a **non-reentrant**
`threading.Lock`. `save_table`/presence writes must NEVER acquire it (would
deadlock — `refresh_unseated_tables` already runs inside the lock and calls
`save_table` repeatedly). The two currently-UNLOCKED entries that will now drive
authoritative presence get explicit locks: `ensure_lobby_seeded` (boot, single
thread — safe) and `run_sim`'s `refresh_unseated_tables` loop. Grep-verified:
lobby.py=0 locks, cash_routes=9, ticker=2.

### D6 — backfill (new `scripts/backfill_presence.py`, run BEFORE the flip)
Existing sandboxes have populated old stores but EMPTY `entity_presence`. One-time
idempotent (`INSERT OR IGNORE`) backfill per sandbox: seats→SEATED
(human→player:, ai→ai:, fish stamp noted), idle→IDLE (+ satellite), off-grid→
SIDE_HUSTLE/VICE, unseated fish→POOL. Pre-existing `seated_and_idle` rows resolved
seated-wins (drop the idle row). Re-runnable as a recovery net (reconstructs
presence from `cash_tables`, which stays written until the deferred view step).

## Reconciler disposition (§F2)
- `_restore_cash_table_binding` (`game_handler.py:1235`): **replace** with an
  `entity_presence` read (`seat_occupant`/`load(player:<owner>)` carries
  table_id+seat_index) — a clean payoff; keep `cash_sessions` fallback for
  pre-flip games.
- `_free_ghost_human_seats` (`cash_routes.py:411`) & `_reclaim_zombie_casino_seats`
  (`casino_provisioning.py:374`): **shrink, don't fully retire** — they catch
  cross-system inconsistencies (game-row absence, deleted personas) presence
  alone can't kill. Convert the now-impossible classes (double-seat) to
  assertions; keep the cross-system sweep.
- `whereabouts.py`: collapses to a single `entity_presence` read; HARD_FLAGS
  (seated_and_idle / double_seat) become dead code.

## Build sequence (each step independently testable; only Step 7 is irreversible)
0. **Schema v129**: add `cash_idle_metadata`; (DEFER the `cash_idle_pool` view per
   D2). Bump `SCHEMA_VERSION`.
1. **`scripts/backfill_presence.py`** (+ `--dry-run`); run on dev, validate.
2. **`cash_mode/presence_transitions.py`** (new, flag-gated no-op) + unit tests.
3. **Wire `save_table`** to call it inside the txn (flag off ⇒ no change).
4. **Wire off-grid** (`ai_side_hustle.py`, `ai_vice_spending.py`) →
   `persist_transition`, flag-gated. Handle START-from-non-IDLE (insert IDLE then
   retry — see Risk 5) instead of swallowing.
5. **Wire lobby**: pass `idle_metadata`, drop redundant `_shadow_reconcile_table`
   calls, lock boot/sim entries; extend `validate_presence_shadow.py` with a
   `--post-flip` mode (presence-as-truth, `MISSING_IDLE`/`STALE_SEAT` no longer
   benign).
6. **Wire human routes**: drop the `_shadow_reconcile_table` calls (now handled in
   `save_table`); rerun `test_shadow_human.py`.
7. **THE ATOMIC CUT** (solo, separately confirmed): `PRESENCE_AUTHORITY_ENABLED =
   True`. Pre-reqs: Steps 0-6 merged & green, backfill run, post-flip audit 0
   unexpected, full suite green.
8. **Reconciler retirement** (cleanup, post-flip).
9. **(Deferred) seat-map / idle-pool view demotion** — make the old stores true
   read-through projections; requires auditing every `table.seats` reader.

## Rollback
Everything before Step 7 is reversible (flag off ⇒ inert). The backfill is
idempotent and reconstructs presence from `cash_tables` (still written), which is
the safety net after the flip. With D2 (no view in the cut), an emergency revert
is `PRESENCE_AUTHORITY_ENABLED = False` + nothing else broken — no view to undo.

## Risks (ranked)
1. **Off-grid START from non-IDLE** (was swallowed in shadow; authoritative now):
   must insert IDLE then retry `START_HUSTLE`/`START_VICE`, or presence desyncs.
2. **`idle_metadata.reason` precision**: callers not passing it get
   `forced_leave` — state correct, reason imprecise. Document per-callsite.
3. **`_restore_cash_table_binding` two-path fragility**: test cold-load explicitly.
4. **Whale slots** (`_spawn_whale_at`/`_wind_down_whale`, casino:1798/1885) — were
   Phase-1 shadow gaps; auto-covered once `save_table` drives transitions, but
   confirm whale slots carry an archetype so origin (POOL vs OFFLINE) is right.
5. **Sim perf**: +1 `SELECT` per `save_table`; measure vs baseline (negligible
   expected, WAL).
6. **`circuit-progression` seeding** writes `cash_tables` directly — gate its
   merge on routing through `save_table`.

## Files
CREATE: `cash_mode/presence_transitions.py`, `scripts/backfill_presence.py`,
`tests/test_cash_mode/test_presence_cutover.py`.
MODIFY: `schema_manager.py` (v129), `cash_table_repository.py` (`save_table`),
`economy_flags.py` (+`PRESENCE_AUTHORITY_ENABLED`), `lobby.py`,
`ai_side_hustle.py`, `ai_vice_spending.py`, `cash_routes.py`, `game_handler.py`,
`sim_runner.py`, `validate_presence_shadow.py` (+`--post-flip`).
