---
purpose: Single-entry handoff for the remaining tournament-circuit work — the live world-tick hook and the React Main Event surface — on top of the now-complete, tested P3 backend (invite lifecycle, chairman-driven cadence, real-persona redistribution, double-presence guard). Start here.
type: guide
created: 2026-06-01
last_updated: 2026-06-02
status: P3.7 (world-tick hook) + P3.8 (React Main Event card) DONE + GREEN (uncommitted on `tournaments`). Flag `TOURNAMENT_CIRCUIT_ENABLED` default OFF — autonomous advance is dormant pending the §6 economy re-sim; the lobby card works today (invite GET is not flag-gated). Remaining = §6 validation + the deferred items (staking/cash-rake/prestige carry-out).
---

> **2026-06-02 — P3.7 + P3.8 landed (uncommitted on `tournaments`).**
> - **P3.7** `flask_app/services/tournament_ticker.py` (`is_autonomous`,
>   `beats_to_world_events`, `advance_owner_tournament`) wired into
>   `ticker_service._tick_sandbox` as `_maybe_tick_tournament`, behind the new
>   `cash_mode/economy_flags.py::TOURNAMENT_CIRCUIT_ENABLED` (default **OFF**).
>   Autonomous vs human is discriminated by the **`human:<owner>` field seat**
>   (no migration — the column idea in §P3.7 was not needed). Structural beats
>   (`tournament_milestone`/`_bubble`/`_winner`, added to `cash_mode/activity.py`)
>   are recorded into the activity buffer so the existing emit block ships them as
>   `world_event`s. Sandbox-lock-only (no registry lock — avoids the `/advance`
>   route's lock-order inversion).
> - **P3.8** `react/.../cash/tournamentApi.ts` + `MainEventCard.tsx`/`.css`, wired
>   into `Lobby.tsx` (fetch on mount + `lobby_tick` + fallback poll; Register →
>   accept → sit → `/game/<id>`; Decline → autonomous). `tournament_*` event types
>   + Trophy glyph added to `types.ts`/`tickerEvents.tsx`.
> - **Tests:** `tests/test_tournament/test_tournament_ticker.py` (8) + 2 gating
>   tests in `tests/test_ticker_service.py`; full `tests/test_tournament/` **239
>   passed**; frontend `tsc` + eslint clean.
> - **Still open:** §6 (re-sim the economy under the per-tournament overlay cadence
>   before flipping the flag on) + the Deferred items below.

# Tournament Circuit — Remaining Work Handoff (START HERE)

You're picking up the multi-table tournament **circuit surfacing** after the
whole **backend** has landed and the full test suite is green. This doc is the
single entry point: what's done, what's left, the exact seams, the decided
model, and the gotchas.

## 0. Read order

1. **This doc** — orientation + build order + seams for the remaining work.
2. **`TOURNAMENT_CIRCUIT_SURFACING.md`** — the original P3 design. **Its time
   model (Q5 freeze-for-days + sim/maintenance split + deadlock recovery) is
   SUPERSEDED** by the simpler model below (§2). Mine it for the lobby-card /
   ticker-beat shape (Q1/Q4) and the entity-relationship model (§3, still valid).
3. **`TOURNAMENT_ECONOMY_ON_STATE_MODEL.md`** + **`P2_BUILD_HANDOFF.md`** — the
   economy substrate (escrow/split, the EconomyChairman). Already built.
4. **`EXP_006_BANK_RESERVE_THERMOSTAT.md`** — the sim that set the overlay
   constants. **Re-run before flipping the economy on in prod** (§6).

## 1. What's DONE (backend, pushed to `origin/tournaments`)

Schema is at **v135**. All green. Commits (newest first): `821f014b`,
`fd19be59`, `ce59cd78`, `2f5cd337`, `bfb8e853`, `514d225f` (P3.5/P3.6), and the
foundation `eddcc5bb`/`95bf4ad6`/`93656d4e` (P3.1–P3.4a).

**The economy (P2 + P3 foundation):**
- `core/economy/economy_signal.py` — the EconomyChairman: `signal()` (one ledger
  snapshot → `EconomyState`), `tournament_funding()` (overlay/rake sizing),
  `should_offer_event()` (FLUSH + cooldown → `EventSpec`; the cadence policy),
  `cash_rake_schedule()` (sibling lever, wiring deferred to cash mode).
- `core/economy/ledger.py` — `tournament(id)` escrow account +
  `record_tournament_buy_in/payout/overlay/return`.
- `flask_app/services/tournament_economy_service.py` — `plan_funding`,
  `apply_buy_in` (escrow-in, 402 affordability), `apply_payout_on_complete`
  (I6 idempotent; `real_persona_ids` credits real `ai:<pid>` bankrolls),
  `verify_tournament_conservation`.

**The play + lifecycle:**
- `flask_app/services/tournament_field.py` — `select_persona_field` (real-persona
  field, `exclude=` for the draft guard), `assign_archetypes`.
- `flask_app/services/tournament_spawn.py` — `spawn_autonomous_tournament`,
  `create_human_tournament` (human in seat 0), `advance_autonomous_tournament`
  (ONE world-tick step: advance N rounds + settle on complete + return reports),
  `settle_autonomous_tournament` (marks the row `complete` → releases
  participants), `draft_exclusions`.
- `tournament/session.py::advance_round()` — one AI-only round (the world-pace
  step).
- `flask_app/services/tournament_invites.py` — `offer`, `active_invite`,
  `accept`, `decline`, `expire_due`, **`maybe_offer_main_event`** (the
  chairman-driven trigger).
- `flask_app/routes/tournament_routes.py` — `GET /api/tournament/invite`
  (lobby card; runs `expire_due` + `maybe_offer_main_event` on load),
  `POST /invite/accept` (stands the human up from cash first), `POST /invite/decline`.
- Schema v135 `tournament_invites` + `TournamentInviteRepository`.

**The double-presence guard (P3.6):** a persona is never in a tournament AND at
a cash table. ENTRY: `draft_exclusions` (seated ∪ already-in-tournament) feeds
`select_persona_field(exclude=)`. DURING: `refresh_unseated_tables(tournament_repo=)`
unions `TournamentSessionRepository.active_participant_pids(owner)` into the
`off_grid` set (same path as vice/hustle). EXIT: settle → row `complete` →
released. HUMAN side: accept route `_leave_cash_if_seated` cashes the human out
of any cash seat first (gated on an open invite).

## 2. The DECIDED model (supersedes the surfacing doc's freeze)

The player's one decision is the **invite**, not a running tournament:

```
offer ──accept──▶ a tournament the human plays IN (real-persona field + buy-in);
                  the human LEAVES their cash seat to enter (cash out → bankroll)
      ──decline─▶ runs autonomously (AI-only)
      ──expire──▶ same as decline (timer lapsed un-accepted)
```

- **No join-after-start.** Accept = starts WITH you; decline/expire = starts
  WITHOUT you. This deletes the "background-joinable tournament" complexity that
  forced the freeze/deadlock machinery in the old Q5.
- **Cadence is the chairman, not a calendar.** A FLUSH bank is the signal to run
  a redistribution event (`should_offer_event`). Self-limiting (the overlay
  drains reserves below the setpoint → next signal isn't FLUSH) + a cooldown.
  Scheduled "open until 8pm" is a future *predictability skin* on top
  (`expires_at` already supports a window), still gated by the chairman.
- **Time while the human is IN a tournament** = the existing `TournamentSession`
  player-gating (your hands are the heartbeat; backing out freezes the field).
  Because the human *left* their cash seat to enter, there's no cash table to
  freeze — which is why we do NOT need the sim/maintenance split or deadlock
  recovery. (The deferred "decision 2" lockstep is moot under leave-to-enter.)
- **Autonomous events advance at WORLD pace** (declined/expired) — a step per
  world tick, like the cash tables (`advance_autonomous_tournament`).

## 3. REMAINING — build order

### P3.7 — world-tick hook (backend, flag-gated, the riskier piece)
Wire the autonomous loop into the live world ticker so a declined/expired Main
Event actually plays out in the background and offers/expiries happen without a
lobby poll. **Flag-gate it (default OFF)** like the other economy work; it only
adds work for sandboxes that have a live autonomous tournament.

Seam: `flask_app/services/ticker_service.py::_tick_sandbox(socketio, owner_id,
sandbox_id)` — after the cash-sim block (`refresh_unseated_tables`) and the
maintenance block, add (behind the flag):
1. `tournament_invites.expire_due(...)` + `maybe_offer_main_event(...)` for the
   owner (so offers/expiries fire on the tick, not just lobby load).
2. Find the owner's active **autonomous** tournament and advance it one step:
   `advance_autonomous_tournament(...)`, then persist + emit beats.

**Key design note (the one real wrinkle):** `spawn_autonomous_tournament` writes
the durable `tournaments` row + funds the escrow but does NOT put the session in
the in-memory `tournament_registry`, and it has no live `game_id`. So the ticker
must (a) locate the owner's active autonomous tournament and (b) distinguish it
from a *human* tournament (which is player-gated and must NOT be auto-advanced).
Recommended:
- "Autonomous" = a tournament with **no human entrant** (`human:<owner>` not in
  `session.field.entries`) and **no live `game_id`**. Add a cheap discriminator
  — either a `kind`/`autonomous` column on `tournaments`, or infer from the
  serialized field (no `human:` seat). A column is cleaner for the ticker query.
- Load + rehydrate the session via `tournament_session_repo` (or the registry's
  rehydrate path), `advance_autonomous_tournament`, then `registry.persist` /
  `session_repo.save` the new state. The settle inside it already marks
  `complete` (releases participants).
- **Beats → ticker:** convert the returned `RoundReport`s to beats
  (`tournament/beats.py::build_beats`, `human_id=None`) and record them as
  `world_event`s via `cash_mode/activity.py::record_event` (new event types,
  e.g. `tournament_knockout` / `_final_table` / `_winner`). The existing
  `_tick_sandbox` emit block then pushes them to `lobby:{owner_id}`. Keep the
  "structural-only" filter (breaks/bubble/final-table/winner), never every hand.
- Hold `get_sandbox_lock(sandbox_id)` across the advance (it mutates the escrow
  on settle).

Tests: an autonomous tournament spawned into a sandbox advances + settles over
several `_tick_sandbox` calls; beats surface as `world_event`s; a human-entered
tournament is NOT auto-advanced; flag OFF = inert.

### P3.8 — React Main Event surface
- `GET /api/tournament/invite` → a **Main Event card** in the cash lobby
  (`react/react/src/components/cash/Lobby.tsx`): prize/overlay, field size, buy-in
  (0 = freeroll in v1), expiry countdown, Register/Decline buttons.
- **Register** → `POST /invite/accept` → then `POST /api/tournament/<id>/sit`
  (existing live bridge) → navigate to `/game/<game_id>`. Surface a confirm
  modal if buy_in > 0 (affordability; the route returns 402 `{required,
  available}`).
- **Decline** → `POST /invite/decline`.
- **Lifecycle beats** already ride the existing `world_event` socket the lobby
  subscribes to (`Lobby.tsx` `onWorldEvent`) — render tournament beats in the
  ticker ("Main Event: final table… X wins +$Y"). Add the new event-type
  rendering.
- `react/react/src/utils/gameId.ts` already has `isTournamentGameId`; `GamePage`
  already routes tournament back-nav to `/tournament`.

### Deferred (not P3.7/P3.8)
- **Staking into entries** (P2 Layer D / "step 5") — bind the cash stake machine
  to `tournament:<id>`, `chips_at_leave` = the real prize, no-carry on bust.
  Still unbuilt.
- **Cash-rake thermostat** — the chairman's `cash_rake_schedule` sibling lever;
  build + sim-model in cash mode.
- **Prestige/relationship carry-out** from results — P4.

## 4. The API surface the remaining work calls (already built + tested)

| Need | Call |
|---|---|
| Is there a Main Event to show? | `tournament_invites.active_invite(invite_repo, owner)` |
| Offer one (chairman-gated) | `tournament_invites.maybe_offer_main_event(...)` |
| Accept / decline / expire | `tournament_invites.accept/decline/expire_due(...)` |
| Advance an autonomous one a step | `tournament_spawn.advance_autonomous_tournament(...)` |
| Who's in a tournament (seat-fill exclusion) | `tournament_session_repo.active_participant_pids(owner)` |
| Distribute the pool | `tournament_economy_service.apply_payout_on_complete(..., real_persona_ids=)` |
| Escrow balanced? | `tournament_economy_service.verify_tournament_conservation(tid, ledger_repo, sandbox_id=)` |

All take injected repos → testable without Flask.

## 5. Gotchas (this project's scar tissue)

- **Tests run in Docker:** `docker compose exec -T backend python -m pytest …`
  (or `python3 scripts/test.py`). Never bare pytest on the host.
- **Route tests hit the LIVE dev DB** (`create_app` → `/app/data/poker_games.db`).
  Pin the sandbox to a throwaway id (see `fixed_sandbox` in
  `test_tournament_routes.py`) and use freeroll/no-write paths so tests don't
  pollute it. Heavy chip logic is tested at the service layer on temp DBs.
- **Schema collisions are real** — we just renumbered v130/v131 (coach vs
  tournament) → v132/133/134 on the dev merge, then added v135. Always renumber
  **above** the current `SCHEMA_VERSION`, dual-path (`_init_db` + `_migrate_vNNN`
  + `migrations` dict), and there's a renumber self-heal block in
  `_run_migrations` for already-migrated DBs.
- **Wall-clock test rot** — `821f014b` fixed a test that seeded relationship
  heat at a fixed past date but read it through real-`utcnow` decay. Any test
  that seeds time-decayed state and reads via the real clock will rot; seed at
  `utcnow`.
- **Docker net-pool exhaustion** — this worktree's `docker-compose.override.yml`
  pins the subnet to `10.123.48.0/24` (.45/.46/.47 are desktop/main/circuit). If
  the stack won't come up with a pool-overlap error, that's why.
- **Idempotency is non-negotiable** (the cash double-settle ~57.5k incident).
  Payout guards on `payout_status`; settle twice = no-op.
- **Double-presence is a CLASS** (`feedback_cash_seat_double_seat_recurrence`) —
  audit every new entry/exit path against it.

## 6. Validate before flipping the economy ON in prod — DONE (2026-06-02)

**The §6 re-sim ran and found the constants did NOT transfer — and shipped the fix.**
EXP_006 tuned a *per-tick* overlay; the production cadence is *per-tournament*. The
re-sim (`scripts/sim_experiments/thermostat_sweep.py --mode tournament_cadence`,
which calls the REAL `should_offer_event` + `tournament_funding`, 3 seeds) showed
the fixed `0.02 × reserves` overlay is ~225× too weak across the 30-min cooldown:
reserves balloon at ~99 chips/tick (vs a baseline 130), barely regulated. **Fix
(validated, shipped):** size each event to **drain reserves back to the FLUSH
setpoint** (`reserves − FLUSH_SETPOINT × holdings`, capped) — a sawtooth matched to
discrete events; held the band across 3 seeds (slope 6.9–12.0, reserves 178k–245k),
conservation-clean. `core/economy/economy_signal.py::tournament_funding` now uses
drain-to-setpoint; tests updated; full write-up in
`docs/experiments/EXP_006_BANK_RESERVE_THERMOSTAT.md` (§6 section). Still wanted
before default-on: one **hands-ON** fidelity run against an aged sandbox (the
modeled-rake faucet understates the real vice faucet — but the lever scales with
reserves, so a bigger faucet just means more chips per event, not a band escape).
v1 Main Events are **freerolls** (buy_in 0); redistribution is bank → field.

## 7. Concurrency / lifecycle hardening (2026-06-02 — DONE, uncommitted)

Four production-readiness fixes landed on top of P3.7/P3.8 (all tested):

- **Cross-worker accept double-debit** — the in-memory sandbox lock doesn't span
  gunicorn workers, so two workers could both charge an accept. Fixed with a
  DB-guarded compare-and-swap: `TournamentInviteRepository.claim()` (`UPDATE …
  WHERE status='offered'`, rowcount) gates accept/decline/expire; only the winner
  builds/charges. `accept()` claims BEFORE the buy-in and `revert_to_offered()`s on
  failure (preserves "insufficient funds keeps the invite open"). The human-spawn
  now deletes its durable row on buy-in failure (no orphan active tournament).
- **Payout `in_progress` reconcile** — a crash mid-distribute left partial credits
  with no retry (the `apply_payout_on_complete` guard BLOCKS re-entry).
  `reconcile_stuck_payout` resumes from the ledger (pays only the unpaid remainder
  per sink via `ledger_repo.payouts_by_sink` — never a double credit), sweeps escrow
  to 0, stamps `complete`. Run by a flag-gated ticker watchdog
  (`_maybe_run_payout_reconcile_watchdog`) + an admin route
  `POST /api/tournament/admin/reconcile-payouts`. The human payout branch was
  reordered ledger-first (uniform with the AI branch) so reconcile can't double-pay.
- **expire_due foreign-sandbox processing** — `list_open_due` / `expire_due` now
  take an optional `sandbox_id`; both callers (lobby GET + ticker) pass the sandbox
  whose lock they hold, so the sweep never spawns into a foreign sandbox's escrow
  un-serialized.
- **One-open-invite partial unique index (v136)** — `CREATE UNIQUE INDEX …
  ON tournament_invites(owner_id) WHERE status='offered'` backs the app-level
  `offer()` guard at the DB; `offer()` now turns the lost-race `IntegrityError` into
  the open invite, not a 500. Migration has a defensive dup-collapse pre-step.

A second review pass added these (all tested):

- **Play routes reject autonomous tournaments** — `/advance`, `/play-out`, `/sit`
  gated only on ownership; an owner holds their autonomous tid (decline returns it),
  so a route could mutate the same in-memory session the ticker advances (the route
  holds the per-tournament lock, the ticker the sandbox lock → data race + a
  route-driven settle misattributing the nominal persona's prize to the human). Now
  409 via `_is_autonomous_record`. **`is_autonomous` was sharpened:** an
  all-synthetic (`P##`) field is the legacy `/register` human path (player drives
  P01 via /sit) — NOT autonomous; only a real-persona field with no `human:<owner>`
  seat is. (Without this, the ticker would also have wrongly auto-advanced
  `/register` tournaments.)
- **Failed payout no longer marks the tournament complete** — `settle_autonomous_
  tournament` only flips `status='complete'` when `payout_status` is terminal
  (`complete`/`skipped`); a payout that THREW leaves `status='active'` so the
  stranded escrow stays visible to the reconcile watchdog (which credits the
  remainder and then releases the field). Previously it marked complete regardless,
  hiding the strand forever.
- **Double-presence exclusion recency-bounded** — `active_participant_pids` only
  counts tournaments touched within `EXCLUSION_MAX_AGE_HOURS` (6h on `updated_at`,
  re-stamped every persist). An abandoned human tournament or a max-rounds-wedged
  autonomous one no longer ghost-seats its whole field out of cash forever. (A
  proper reaper is still deferred; the bound is the cheap correctness fix.)
- **`draft_exclusions` fails CLOSED** — a seat/participant scan error now raises
  `DraftScanError` and the spawners abort (return None) instead of fielding from a
  partial exclusion set (under-exclusion would draft a seated persona — the
  dangerous direction).
- **Atomic payout claim** — `apply_payout_on_complete` uses
  `TournamentSessionRepository.claim_payout()` (CAS `pending`→`in_progress`) instead
  of the read→check→set, so a missed lock can't let two callers both distribute.
- **decline/expire report success when the invite is consumed** — even if the
  autonomous field can't be spawned (too few personas), the dismissal succeeded;
  `_resolve_autonomously` returns a marker (`tournament_id: None`) rather than None
  (which the route mapped to a misleading 404).
- **Persist before beats** — `advance_owner_tournament` persists the advanced
  session immediately after the advance (before deriving beats), so a beat-building
  hiccup can't leave a stale `session_json` / drop the climactic winner beats.
