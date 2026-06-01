---
purpose: Single-entry handoff for building P2 — the multi-table tournament economy — on top of the now-landed cash-mode chip-custody ledger substrate. Start here.
type: guide
created: 2026-06-01
last_updated: 2026-06-01
status: READY TO BUILD — design complete, substrate landed, no code written yet
---

# P2 Tournament Economy — Build Handoff (START HERE)

You are a fresh context picking up the multi-table tournament **economy** (P2):
buy-ins → bank-overlay prize pools → payouts → staking, as a self-regulating
bank-reserve **thermostat**. The design is complete and the substrate it depends
on has **landed**. This doc is the single entry point; it tells you what's done,
what to read, the build order, the decided contracts, and the gotchas.

## 0. Read order (don't skip)

1. **This doc** — orientation, build order, contracts, gotchas.
2. **`TOURNAMENT_ECONOMY_ON_STATE_MODEL.md`** — the canonical design: how the
   tournament economy maps onto the cash-mode unified ledger, the escrow/split
   contract, the `EconomyChairman` signal. **This is the authority.**
3. **`MULTI_TABLE_TOURNAMENT_P2_ECONOMY.md`** — the original blueprint. PARTIALLY
   SUPERSEDED (it has a banner). Mine it ONLY for the **funding regimes**, the
   **payout curve**, and the **file:line integration surface** — version-correct
   everything (it predates the v131 merge).
4. **`TOURNAMENT_CIRCUIT_SURFACING.md`** — P3 (how tournaments surface in circuit
   mode). NOT P2, but it locks the escrow/split + world-pause model P2 must honour.
5. **`EXP_006_BANK_RESERVE_THERMOSTAT.md`** — the sim that validated the overlay
   control law (~0.08 setpoint). Constants are sim-tuned, not guessed.

## 1. Where things stand (2026-06-01)

- **Branch:** `tournaments` (pushed to `origin/tournaments`). `development` has been
  merged in (commit `1acea510`); the branch is current with dev's work.
- **Schema:** **v131.** P1 persistence (tournaments table = v130), tracker drop = v131.
  The economy's first migration is **v132**.
- **Substrate LANDED (the dependency is gone):** dev cut over the **chip-custody
  machine** — a *ledger projection*, not a parcel-state store. In
  `core/economy/ledger.py` + `poker/repositories/chip_ledger_repository.py`:
  - Accounts: `bank()`, `player(owner_id)`, `ai(pid)`, `seat(game_id)`.
  - `record()` (central-bank creations/destructions) + `record_transfer()`
    (entity↔entity, `TRANSFER_REASONS`, drift-invisible).
  - `record_player_buy_in/cash_out` AND `record_ai_buy_in/cash_out`
    (`player/ai → seat(game_id)` and back).
  - `chip_ledger_repository.balance_of(account)` — bankroll is ledger-derived (D2).
  - Reaper settles-before-delete (a non-empty `seat()` only retires via a
    settlement transfer; never zeroed). Gated by `CHIP_CUSTODY_ENABLED` (dev-on).
- **No P2 code written yet.** This is greenfield on a proven substrate.
- **Tournament engine + persistence already exist:** `tournament/` package
  (`TournamentSession`, `TournamentField`, director, seating, blinds),
  `tournaments` table + `TournamentSessionRepository`, the live bridge
  (`flask_app/handlers/tournament_handler.py`, `tournament_game_builder.py`),
  routes (`flask_app/routes/tournament_routes.py`). Funny-money play is done; P2
  adds the **real-chip** layer around it.

## 2. The one-paragraph mental model

A tournament is an **event inside the player's single circuit sandbox** (NOT a
nested sandbox — §3 of the surfacing doc). Two chip layers: the **funny-money
`TournamentField`** (isolated, self-conserving, `field_size × starting_stack`,
already built) and the **real chips** (buy-in/overlay/rake/payout) which live in
the **sandbox's own ledger**. The bridge is one new ledger account, `tournament:<id>`,
a sibling of the shipped `seat:<game_id>`. The **tournament runner stays a pure
funny-money function**; the **circuit sandbox is the sole real-chip authority.**

## 3. The decided contracts (build to these)

**Escrow + payout-split** (the spine — see ECONOMY_ON_STATE_MODEL §"escrow + payout-split"):
1. **Escrow-in** (sandbox, at registration): move each entrant's buy-in INTO
   `tournament:<id>`. Human/AI buy-in = `record_transfer` (`player/ai → tournament:<id>`).
   Bank overlay / bank-seed / freeroll seed = a bank-pool **DRAW** into the escrow.
   **Classify overlay vs buy-in HERE, by reason** — overlay counts in drift, a
   buy-in is a drift-invisible transfer. (The `tournament:<id>` counterparty alone
   does NOT distinguish them.)
2. **Run** (the existing engine, funny money): emits a **payout split** =
   `[(recipient, percent_of_purse), …]` summing to 1.0. Zero real-chip knowledge.
3. **Distribute** (sandbox, at COMPLETE): drain the escrow — `tournament:<id> →
   recipient` for `round(pct × purse)`. Escrow nets to 0.

**Invariants:**
- `tournament:<id> == Σ buy_ins + Σ overlays` (after escrow-in) `== Σ payouts + rake → 0` (after distribute).
- **Rake** = a tuple in the split (`(rake_sink, pct)`) — simplest; or pre-skim at escrow-in.
- **Rounding residual → top finisher** (no leakage).
- **Idempotent distribution (I6):** mark the escrow drained (terminal flag) so a
  retry/restart can't double-pay. Generalise the `ended_at`-style guard; this is
  the same discipline as the cash settle-before-delete reaper.
- **Cross-scope audit:** career bankroll is GLOBAL, escrow + audit are
  SANDBOX-scoped. Make the player side of escrow-in/payout an explicit
  **cross-scope transfer with both sides audited** (or treat the career bankroll
  as a ledger owner attributing to the sandbox event) — else the escrow looks
  "funded from outside the sandbox." (Codex review finding.)

**EconomyChairman** (the one genuinely-new shared piece):
- A pure `economy_signal.py` read-model over the ledger: `signal(ledger_repo, *,
  sandbox_id) -> EconomyState(reserves, holdings, ratio, regime)`. `reserves` =
  `compute_bank_pool_reserves`; `holdings` = ledger-derived (`balance_of`).
- Two pure policy fns consume it: `tournament_funding(state, …) -> FundingPlan`
  (overlay/rake regimes around the ~0.08 setpoint, proportional overlay) and
  `cash_rake_schedule(state)` (the sibling cash lever — build in cash mode later).
- **One snapshot per decision, coordinated actuators** — both levers read the same
  `EconomyState`; don't let them each over-correct the same reserves (oscillation).
- Pure + **caller holds `get_sandbox_lock`** across read-signal → decide →
  apply-transfers (atomic).

## 4. Build order (each its own commit + green tests)

0. **DONE** — unified ledger substrate (on `development`, merged in). Consume it.
1. **EconomyChairman** — `economy_signal.py` (or `cash_mode/economy_chairman.py`):
   `EconomyState` + `tournament_funding` + `cash_rake_schedule`, pure, over the
   ledger read-model. Unit-test flush/neutral/empty + the EXP_006 setpoint.
2. **Schema v132 + `tournament(id)` escrow account + ledger reasons.** Add
   `tournament(game_or_tournament_id)` account helper (mirror `seat()`),
   `record_tournament_buy_in/payout` (mirror `record_ai_buy_in/cash_out`), and the
   overlay-draw / rake-deposit reasons. Schema v132: add `buy_in / rake /
   bank_overlay / prize_pool / payout_status` columns to the `tournaments` table
   (follow the v130/v131 dual-path convention — table in `_init_db` AND a
   `_migrate_v132_*` AND the `migrations` dict; `SCHEMA_VERSION = 132`).
3. **Buy-in flow** at `register_tournament` (`tournament_routes.py`): parse +
   validate buy-in, affordability gate (402), debit → escrow, overlay/rake,
   rollback on failure. Idempotency throughout.
4. **Payout** as an I6 terminal transition at COMPLETE — wire at the boundary
   (`tournament_game_builder.py::tournament_hand_boundary`), the **play-out route**
   (`tournament_routes.py::play_out` — the gap where the boundary hook isn't
   called), AND the headless director completion (autonomous path). One service,
   `payout_status` guard, distribute-the-escrow-per-the-split.
5. **Staking into entries** — reuse the cash stake machine (state-model Tier 2)
   bound to `tournament:<id>`; `chips_at_leave` = the real prize; no-carry on bust.

## 5. Integration surface (verified — version-correct from P2_ECONOMY §"Integration surface")

The file:line hooks in `MULTI_TABLE_TOURNAMENT_P2_ECONOMY.md` are correct in shape;
the doc predates the merge so re-confirm line numbers. The load-bearing ones:
- **Buy-in:** `tournament_routes.py::register_tournament` (after `registry.persist`).
- **Payout:** `tournament_game_builder.py::tournament_hand_boundary` on COMPLETE,
  **and** `tournament_routes.py::play_out` (the play-out gap), **and** the headless
  `TournamentDirector` completion.
- **Finish order:** `tournament/field.py::Elimination.finishing_position` (1=winner),
  `session.winner()` / `human_rank()` / `is_complete()`.
- **Funny-money conservation:** `TournamentField.assert_conservation()` — leave it
  ALONE; it's orthogonal to real chips.

## 6. Validate before flipping on (the thermostat is sim-gated)

Constants (overlay %, setpoint, rake schedule) are **sim-tuned, not guessed**.
EXP_006 validated a proportional-overlay controller parking reserves at ~0.08.
Before turning any thermostat on in prod, re-run the economy sim with the new
levers (cf. `reference_cash_sim_ab_paired`, `project_casino_economy_cycling` —
measure drain RATE). The cash-rake sibling lever shares the Chairman signal and
must be sim-modeled together (build it in cash mode, not P2).

## 7. Gotchas (this project's scar tissue)

- **Schema collisions are real.** We just resolved a v123/v124 collision on the
  merge (tournaments vs development both used them). Always renumber your migration
  **above the current `SCHEMA_VERSION`** and follow the dual-path convention.
- **Tests run in Docker:** `python3 scripts/test.py` or `docker compose exec -T
  backend python -m pytest …`. Don't run bare pytest on the host.
- **Import-copy / xdist pollution:** prefer reading `extensions.X` live over
  `from ..extensions import game_repo`. Green-alone / red-combined ⇒ suspect this
  (see `tests/CLAUDE.md`).
- **Integration tests leak a real commentary/Groq LLM call** from the
  psychology-narration path — stub it (it's flagged, pre-existing).
- **`data/` is root-owned;** reset the worktree DB via Docker (`docker compose run
  --rm --no-deps --entrypoint bash backend -c "cd /app/data && mv poker_games.db
  …; rm -f poker_games.db-wal poker_games.db-shm"` then `docker compose up -d
  backend`). The DB rebuilds fresh from `_init_db` at the current `SCHEMA_VERSION`.
- **Idempotency is non-negotiable** (the cash double-settle ~57.5k phantom-chip
  incident). Status flag before any bankroll write; settle twice = no-op.

## 8. What is NOT P2 (don't pull it forward)

- **Circuit surfacing** (the lobby card, the registration window, the world-tick
  pause, the entry UX) = **P3**, `TOURNAMENT_CIRCUIT_SURFACING.md`. P2 must make the
  chip flows correct for a tournament run head-lessly via the director; P3 spawns
  and surfaces them.
- **Cash-rake thermostat** = build in cash mode (shares the Chairman signal).
- **Prestige / relationship carry-out** from results = P4.
