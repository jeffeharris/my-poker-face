---
purpose: Design + phased build plan for "tournaments as a draw" — AI personas leaving cash tables to enter tournaments, pulled by an attractiveness model. Phase A (the conservation-safe call-up vacate) is shipped; B–D are the plan.
type: design
created: 2026-06-03
last_updated: 2026-06-03
---

# Tournaments as a Draw

## Premise (the why)

A tournament is **the bank's economic-redistribution mechanism**: when the bank
reserve is flush, the EconomyChairman offers a Main Event whose **prize pool is
funded by the overlay** (drained from excess reserves) — chips flow bank → prize
→ finishers' `ai:<pid>` bankrolls. The prize is **real** (a freeroll = free
*entry*, not a zero prize).

It should also be a **draw** — pull AIs off cash tables the way it pulls a human:
- **Low-prestige personas:** the shot at sitting with the "bigs" and making a name
  (renown upside, variance-positive).
- **Bigs:** renown + regard from winning (cash prize secondary).

Today's behavior is the *opposite*: `tournament_spawn.draft_exclusions` deliberately
**excludes** cash-seated personas (the double-presence guard fails closed against
drafting a seated persona). This feature inverts that — drawn personas **leave**
their cash seats to join.

## Settled design decisions (from the product owner)

- **Prize is the primary draw + the redistribution lever** (same overlay knob);
  renown/regard is the secondary/status draw.
- **Renown/regard = granted to winners** (winner + paid places) — a *minimal*
  grant-on-win via the existing prestige path, NOT the full deferred P4 carry-out.
- **No win-biasing for v1** — any field, any winner ("I don't care who wins").
  Future lever (not built): if wealthy AIs dominate on skill, gate **entry to the
  bottom 90%** by bankroll.
- **v1 = Main Event only**; the draw model is general so a weaker-prize tier plugs
  in later.
- **Flag-gated** behind `TOURNAMENT_DRAW_ENABLED` (separate from
  `TOURNAMENT_CIRCUIT_ENABLED`), default off.
- **Lean tracking**: reserve/vacate state lives in JSON columns on
  `tournament_invites` (no new table, no new presence-machine state).

## The hard constraint that shapes everything

**A tournament field is LOCKED at spawn** — `TournamentSession.__init__` builds
`entries`/`field`/`seating` once via `build_initial_state`; there is no
"add-a-participant-to-a-running-tournament." So "trickle in" cannot mean
incrementally seating a live tournament. The model is:

> **RESERVE** the drawn field at offer time → **VACATE** the reserved
> cash-seated personas off their seats during the window → **SPAWN** the
> tournament once everyone has gathered.

- **Autonomous path:** trickle-vacate over the invite's `expires_at` window
  (ticker-driven); spawn at window end.
- **Human-accept path:** build the human's live table immediately (human +
  already-idle members); reserved cash-seated members finish their **current**
  hand, then vacate, and the existing `reconcile_live_table` adds them as
  balanced-in seats.

## The draw / attractiveness model (to sim-tune)

Score every eligible persona; take the **top N = field_size** (deterministic, not
probabilistic). Starting shape (weights are sim-tunable):

```
draw = w1·prize_appeal + w2·renown_appeal + w3·field_appeal − w4·cash_comfort
```
- `prize_appeal` ~ prize_pool / own_bankroll (a big prize relative to your stack
  pulls harder → naturally pulls small fish → redistribution-aligned).
- `renown_appeal` ~ renown/regard on offer × the persona's status-seeking trait.
- `field_appeal` ~ are high-renown "bigs" already in the field (pulls small fish).
- `cash_comfort` damps a persona who's winning/settled at a good cash seat.

`renown_appeal` degrades gracefully to 0 when `RENOWN_V2_PERSIST_AI` is off (draw
falls back to prize + field terms — fine for v1).

## Phases

### Phase A — conservation-safe "called-up" vacate ✅ DONE (`289810b7`)
The riskiest seam, proven in isolation, wired to nothing.
- `cash_mode/movement.py`: `CALLED_UP` decision + `called_up_pids` kwarg on
  `refresh_table_roster` → unconditional leave (skips coercions/take_stake/rebuy),
  no idle-pool add (like `go_vice`), seat chips settle via the existing `from_seat`
  BankrollChange (caller's seat-diff departed-credit handles conservation — **no
  new settle path**). New `RosterRefreshResult.called_up`. Inert when None.
- `cash_mode/whereabouts.py`: `STATUS_TOURNAMENT` / `STATUS_TOURNAMENT_BOUND`
  constants (defined; not surfaced yet).
- `cash_mode/economy_flags.py`: `TOURNAMENT_DRAW_ENABLED` (default False).
- Test: `tests/test_cash_mode/test_movement.py::TestCalledUpVacate`.

### Phase B — draw scorer + reserve/spawn lifecycle (IN PROGRESS)
- **B1 ✅ DONE + PUSHED (`0353c727`)** — `flask_app/services/tournament_draw.py`:
  pure `score_draw` + `rank_field` (+ `DrawInputs`/`DrawWeights`), fully
  unit-tested (`tests/test_tournament/test_tournament_draw.py`). Pure, unused by
  any caller yet.
- **B2 ⚠️ COMMITTED LOCAL, NOT PUSHED, UNVERIFIED-BY-SUITE (`dbc88bc0`)** — schema
  **v148**: `reserved_pids` + `vacated_pids` JSON cols on `tournament_invites`
  (`_init_db` + guarded `_migrate_v148_invite_reserved_pids` + dict entry,
  SCHEMA_VERSION→148). `TournamentInviteRepository`: `create(reserved_pids=…)`,
  `_row_to_dict` deserializes (keys-guarded), `set_reserved_pids` /
  `set_vacated_pids` / `reserved_pids_for_owner`. ruff clean but the dev container
  was OOM-killing test runs at handoff time — **FIRST next step: re-run on a
  stable container** `test_tournament/test_invites.py` + `test_repositories/test_schema_manager.py`
  + a fresh-build schema smoke (SCHEMA_VERSION==148, cols present), then **push**.
- **B3 — ✅ DONE (2026-06-03, verified, uncommitted)** — the effectful wiring,
  fully inert with `TOURNAMENT_DRAW_ENABLED` off (default):
  - `flask_app/services/tournament_draw.py`: effectful `build_draw_inputs` +
    `DrawContext` (bundles the 5 repos as ONE optional dep). Per-term best-effort
    reads: bankroll `load_ai_bankroll_current`; renown `load_renown_v2_peaks`
    field-normalized 0..1 (both renown terms → 0 when `RENOWN_V2_PERSIST_AI`
    off); `cash_comfort` = **seat-stack depth** `clamp(seat_chips/starting_stack)`;
    `prize_pool` via `econ.plan_funding(...)` (read-only); `status_appetite` =
    the `ego` anchor via a NEW side-effect-free `PersonalityRepository.load_ego_by_ids`
    (no `times_used` bump). Pure scorer above it untouched.
  - `tournament_invites.offer()`/`maybe_offer_main_event()`: optional `draw_ctx`;
    on the happy create path `_reserve_draw_field` (flag-gated, best-effort —
    never breaks the offer) → `rank_field` → `invite_repo.set_reserved_pids`.
    New `draw_context()` builder. `accept`/`_resolve_autonomously` pass
    `invite['reserved_pids']` + `invite_repo` to the spawners.
  - `tournament_spawn.draft_exclusions(invite_repo=…)`: unions
    `reserved_pids_for_owner` (fail-closed). No-op for the consuming spawn (invite
    already claimed → empty). `reserved_pids_for_owner` got a PRAGMA column guard
    so it returns `set()` (not OperationalError→DraftScanError→abort-all-spawns)
    on a pre-v148 DB — the union fires on every spawn, not just flag-on.
  - `select_persona_field(scored_order=…)`: orders eligible (exclude-subtracted)
    personas by draw rank, reserved-first, random-fills the rest. A
    reserved-but-still-seated persona is skipped (fail-closed) until Phase C
    vacates it. Spawners thread `reserved_pids` → `scored_order`.
  - Call sites wired: `ticker_service` + `tournament_routes` build a `DrawContext`
    from `extensions.*` and pass it (inert behind the flag).
  - Tests: `test_tournament_draw.py::TestBuildDrawInputs`,
    `test_persona_field.py::TestScoredOrder`,
    `test_invites.py::{TestDrawReserve,TestDraftExclusionsReserved}`. Full
    `test_tournament/` + `test_cash_mode/test_movement.py` + schema = **395 green**;
    ruff clean. **Defaults noted:** renown read = peak (only thing exposed);
    `renown_on_offer` = `DEFAULT_RENOWN_ON_OFFER` const (Phase D sizes the real
    grant); reserve = top `field_size`; `field_top_renown` = field-relative
    binary (1 when any big in pool). All sim-tunable.

### Phase C — ticker trickle-vacate + whereabouts surfacing ✅ DONE (2026-06-03, verified, uncommitted)
Fully inert with `TOURNAMENT_DRAW_ENABLED` off (default). Owner decisions baked
in: **both sites vacate** (lobby + the human's live table), and **NO early
spawn** — the human keeps the full registration window; their seat is held and
filled by an AI at `expires_at` (the existing `expire_due` path), never an early
AI-only start.
- **Gate helpers** (`tournament_invites.py`): `open_invite_for_gather` /
  `bound_pids` return the open invite's `reserved_pids` ONLY when
  `TOURNAMENT_DRAW_ENABLED` AND the invite has an `expires_at` — the
  **no-stranding guarantee** (only gather when a spawn at expiry is guaranteed,
  so a vacated persona is never left in limbo).
- **Site A — `refresh_unseated_tables(called_up_pids=…)`** (`lobby.py`): adds the
  reserved set to the `off_grid`/`unavailable` exclusion (so the global greedy
  fill never re-seats a reservation) AND passes it to the per-hand
  `refresh_table_roster`; new `agg_called_up` threads `per_hand.called_up` into
  the synthesized `RosterRefreshResult.called_up`. `ticker_service._tick_sandbox`
  computes `called_up` from the invite **inside the sandbox lock**, captures the
  results, and `_record_vacated` writes the leavers to `vacated_pids`.
- **Site B — the human's live table** (`game_handler.py`): new
  `_tournament_bound_pids` unioned into `off_grid` at all three seat-fill paths
  (`_refill_cash_seats`, `select_rejoin_candidates`,
  `_refresh_lobby_table_for_session`); the hand-boundary refresh also passes
  `called_up_pids`. So a reserved opponent drifts to the Main Event instead of
  being re-seated.
- **`whereabouts.build_whereabouts`** (optional `tournament_session_repo` /
  `tournament_invite_repo`): `STATUS_TOURNAMENT` (in a running tournament),
  `STATUS_TOURNAMENT_BOUND` (reserved + vacated, en route), `STUCK_SEATED_AND_TOURNAMENT`
  (hard — true double-presence) + `STUCK_TOURNAMENT_BOUND_AND_SEATED` (soft —
  reserved + still seated past expiry, i.e. missed the gather). Derives state from
  LIVE seat status, not `vacated_pids`.
- **Double-presence guard**: the fill-exclusion is applied to EVERY seat-fill
  candidate pool (lobby global greedy + all 3 human-table paths), so a vacated
  persona is never re-seated; the spawn-time `select_persona_field` exclude is the
  backstop. `vacated_pids` is observability-only (not a spawn gate); human-table
  vacations are intentionally not folded into it (avoids a cross-path invite write
  race). Tests: `test_cash_whereabouts.py` (tournament statuses/flags + inert),
  `test_invites.py::TestBoundPidsGating`, `test_offgrid_not_seated.py::TestSelectRejoinExcludesTournamentBound`.
  Regression: full `test_cash_mode/` + `test_tournament/` + `test_ticker_service.py`
  + `test_cash_whereabouts.py` = 1553 green; ruff clean.

### Phase D — winner renown/regard grant
- `tournament_spawn.settle_autonomous_tournament` (+ the human payout path): after
  payout, grant a renown/regard bump to winner + paid places via
  `prestige_snapshots.record(formula_version='tournament_v1')` — additive, no new
  table. Sim-tune the magnitudes.

## Riskiest seams (scar tissue)
1. **Cash-leave conservation** (Phase A — DONE): never invent a settle path; reuse
   the seat-diff departed-credit. Verified green against lobby-conservation +
   seat-occupancy suites.
2. **Double-presence**: a persona must never be cash-seated AND in the
   tournament/reserved at once. `draft_exclusions` + the `called_up`-no-idle-add +
   the whereabouts stuck flag are the guards. Audit every new path against the
   recurring ghost-seat bug class.
3. **All-three-call-sites threading** (Phase C): if `called_up_pids` isn't wired
   into the human's live-table refresh, a reserved persona stays live there
   (reserved-AND-seated). The de-risk is the integration test + holding the lock.

## Key seams (file refs)
- Draw selection: `flask_app/services/tournament_field.py::select_persona_field`,
  `tournament_spawn.py::{draft_exclusions, spawn_autonomous_tournament, create_human_tournament}`.
- Invite lifecycle/timing: `tournament_invites.py` (`offer`/`accept`/`expire_due`,
  `expires_at`), `poker/repositories/tournament_invite_repository.py`.
- Ticker: `ticker_service.py::_tick_sandbox` + `tournament_ticker.py`.
- Cash leave/settle: `cash_mode/movement.py::refresh_table_roster` (the `called_up`
  primitive), `flask_app/handlers/game_handler.py` (`_refill_cash_seats`, seat-diff
  `departed_pids` → `_credit_departed_ai_bankrolls`).
- Whereabouts: `cash_mode/whereabouts.py`.
- Tournament field lock: `tournament/session.py`, `tournament/director.py::build_initial_state`,
  `flask_app/handlers/tournament_handler.py::reconcile_live_table`.
- Renown: `poker/repositories/prestige_snapshots_repository.py`, the renown-v2 cols.
- Prize/overlay (prize_appeal source): `core/economy/` (`economy_signal`, `ledger`).
