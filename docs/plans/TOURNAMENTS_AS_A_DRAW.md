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

### Phase B — draw scorer + reserve/spawn lifecycle (NEXT)
- **Schema v148**: add `reserved_pids TEXT` (+ `vacated_pids TEXT`) JSON columns to
  `tournament_invites`. Repo `update_reserved_pids` / `update_vacated_pids` +
  deserialize in `load()`.
- **`flask_app/services/tournament_draw.py`** (NEW): pure `score_draw` + `rank_field`
  + an effectful `build_draw_inputs` (injected repos: bankroll, prestige/renown,
  cash_table). Unit-test the pure core (`tests/test_tournament/test_tournament_draw.py`).
- **`tournament_invites.offer()` / `maybe_offer_main_event()`**: after writing the
  invite, run the scorer → store the top-N as `reserved_pids` (flag-gated).
- **`tournament_spawn.draft_exclusions`**: union the open invite's `reserved_pids`
  (+ later `en_route`) so a reserved persona isn't drafted into a concurrent
  tournament.
- **`spawn_autonomous_tournament` / `create_human_tournament`**: accept the
  reserved field instead of a fresh random shuffle (add `include`/`scored_order`
  to `select_persona_field`).

### Phase C — ticker trickle-vacate + spawn + whereabouts surfacing
- **`ticker_service._tick_sandbox`**: thread the open invite's
  `reserved_pids − vacated_pids` (intersected with currently-cash-seated) as
  `called_up_pids` into the roster-refresh call-sites (unseated tables, the human's
  live table via `_refill_cash_seats`/`_refresh_lobby_table_for_session`, rejoin)
  — **all three**, or a reserved-and-seated persona is a split-brain. Record
  `vacated_pids` from the refresh result's `called_up`. **Hold the sandbox lock**
  for the vacate (it touches the ledger + table row + idle pool).
- Spawn when `reserved ⊆ vacated` OR `expires_at` elapsed.
- **`whereabouts.build_whereabouts`**: surface `STATUS_TOURNAMENT` (from
  `active_participant_pids`) and `STATUS_TOURNAMENT_BOUND` (from `reserved_pids`),
  + a `STUCK_*_AND_SEATED` flag for reserved-but-still-seated past expiry.

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
