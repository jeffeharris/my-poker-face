---
purpose: Implementation blueprint for P2 of the multi-table tournament feature — the economy layer (buy-ins, bank-overlay prize pools, payouts, staking) designed as a self-regulating bank-reserve thermostat
type: design
created: 2026-05-30
last_updated: 2026-06-01
status: SUPERSEDED in part — read P2_BUILD_HANDOFF.md + TOURNAMENT_ECONOMY_ON_STATE_MODEL.md first
---

# Multi-Table Tournament — P2 Economy Blueprint

> **⚠️ PARTIALLY SUPERSEDED (2026-06-01). Do NOT build straight from this doc.**
> Start at **`P2_BUILD_HANDOFF.md`** (single entry point), then
> **`TOURNAMENT_ECONOMY_ON_STATE_MODEL.md`** (the corrected ledger/idempotency/
> signal framing). The cash-mode **chip-custody machine has since LANDED** on
> `development` and merged into `tournaments` (schema **v131**), which changes the
> substrate this doc was written against. Specifically, what is now WRONG here:
> - **Schema "v124"** everywhere → the branch is at **v131**; the economy migration
>   is **v132+** (`_migrate_v124_add_tournament_economy` / `migrations[124]` would
>   collide).
> - **"Two conservation statements" + a separate `verify_tournament_conservation`**
>   → collapses into the state model's single **I1** over the unified ledger; the
>   checksum becomes a query, not a subsystem.
> - **Parcel-state framing** (`IN_BANKROLL → COMMITTED → …`) → custody landed as a
>   **ledger projection**: `tournament:<id>` is a ledger account; its balance IS the
>   escrow amount. Mirror the shipped `seat:<game_id>` + `record_ai_buy_in/cash_out`.
>
> **What is STILL VALID here** and worth mining: the funding regimes (flush overlay
> / neutral / empty rake), the payout curve (top ~30%, front-loaded, all ITM
> finishers), the autonomy decision, and the **file:line integration surface**
> (§"Integration surface (verified)") — version-correct it, but the hooks are right.

P1 (persistence) shipped in layers A/B/C on branch `tournaments`. This is the
original design + build plan for **P2: the economy**, from a three-front
exploration pass (real-chip ledger, stakes system, bankroll/hook points) plus a
code-architect blueprint, reshaped by the product decisions below.

> Read `MULTI_TABLE_TOURNAMENT_PLAN.md` (§"tournament as a ledger actor") first.
> The exploration that grounds every file:line here is summarized in
> §"Integration surface (verified)".

## The core idea: the tournament is an economy *thermostat*

The headline departure from a vanilla "buy-in → prize pool → payout" design:
**the prize pool's funding source is a function of the bank's reserves vs. total
player holdings**, not a constant. The tournament is the *fast* counterweight to
the slow whale/fish chip drip — a way to move large amounts of chips in one event
and self-correct the closed economy. (This is the realization of the
`project_casino_economy_cycling` idea: "tournament buy-ins → prize pool → cycles
to winner.")

```
prize_pool = human_buy_in + Σ(ai/tourist_buy_ins) + bank_overlay − rake

  bank FLUSH   →  big overlay, rake 0      (drains coffers into circulation)
  bank NEUTRAL →  buy-ins only             (no net bank flow)
  bank EMPTY   →  overlay 0, rake > 0      (refills coffers)
```

It is **self-regulating**: a flush-bank overlay tournament drains reserves, which
automatically shifts the next event toward player-funded + rake, which refills.
A thermostat, not a money printer.

### The tournament runs with or without the human

A tournament is **autonomous** — it exists primarily as a fund-redistribution
event among the AI field, and runs whether or not the human plays. P1's headless
AI-only `TournamentDirector` already supports exactly this; P2 makes the *chip
flows* real. The human is an **opt-in entrant**: pay the buy-in to take a seat and
be prize-eligible, or sit out and receive nothing — the event happens regardless.

Because the goal is **redistribution**, the prize pool is paid out across the
field's top finishers per the curve — **AI finishers get paid too**, not just one
winner. That is the actual mechanism that cycles chips from the busted many to the
finishing few (and, with a flush-bank overlay, injects fresh bank chips into the
field). One AI getting rich is fine — they then bleed it back to grinders in cash
mode; the tournament is the *fast* redistribution step in that loop.

The **buy-in is aligned to the prize pool**: it is the per-seat share, so a bigger
pool (flush-bank overlay, higher tier) means a bigger buy-in to access it. The
human pays the same seat price as the field, or doesn't play. (Who *spawns*
autonomous/scheduled redistribution tournaments is **P3** circuit/scheduler work;
P2 builds the economy so any tournament — human-in or AI-only — moves chips
correctly, validated headlessly via the director.)

### Locked product decisions (2026-05-30)

| Decision | Choice | Notes |
|---|---|---|
| **Autonomy** | Tournament runs **with or without** the human | AI-only redistribution by default; human is an optional entrant. Engine (headless director) already supports it. Autonomous *spawning* = P3. |
| **Entry model** | Human **opt-in** buy-in, **aligned to the prize pool** (per-seat share) **+ bank overlay** | Pay the seat price to be prize-eligible, or sit out and get nothing. Bigger pool ⇒ bigger buy-in. Not a free-money printer. |
| **Payout curve** | Top **30%** of field, front-loaded (~38/24/15…), paid to **all in-the-money finishers (AI + human)** | The redistribution mechanism. Tunable via config, no code change. |
| **Rake (tournament)** | Default **0**, mechanism present but dormant | Turns on when the bank needs refilling; foundation for a future "wealth-tax" variant. |
| **Funding** | **Dynamic** by bank-reserve / player-holdings ratio | A pure policy function `compute_tournament_funding(...)` is the "brain". |
| **AI-entrant funding (v1)** | Bank-seeded, **net-zero** (default) | Bank creates AI entries + reclaims on bust; conservation-trivial. Tourist *real* buy-ins (drain AI bankrolls into the pool) are a thermostat extension — see §Deferred. |
| **Staking into entries** | Wealthy (AI or human) **stake players for a cut** | Both a feature (Layer D) and a redistribution lever — rich chips flow into the field and back with a cut. Reuses the existing stakes system. |
| **Wealth-tax entry** | **Deferred** as a named variant | Layers on top of the buy-in+overlay base; not the v1 default. |

### Sibling economy levers (model together, build separately)

P2 is one lever in a family of **demand-driven economy thermostats**. These are
*not* all P2 build scope, but the economy must be **modeled (sim) as a whole** so
the constants are tuned, not guessed (cf. `reference_cash_sim_ab_paired`,
`project_casino_economy_cycling` — measure drain *rate*, not the slow lobby sim):

1. **Tournament thermostat** (this doc): overlay drains a flush bank, rake refills
   an empty one.
2. **Cash-table rake thermostat** (sibling, cash-mode — *model here, build in
   cash mode*): rake that **comes on/off and scales by stake tier based on need**.
   Today rake fires only at the top tier (`cash_mode/economy_flags.compute_rake`,
   `RAKE_STAKE_BIG_BLINDS` — verify the live %). The lever: when the bank runs low,
   **raise the $1000 rake (≈1%→2%) and switch on a ≈1% rake at $200 (and $50 only
   if things get dire)**; switch back off when reserves recover. Higher tiers rake
   more. This needs the same `bank_reserves / player_holdings` signal as the
   tournament policy — share the signal function.
3. **Staking as redistribution** (Layer D + cash): wealthy stake others for a cut,
   moving idle rich chips into active play.

The shared primitive across all three is a single **economy-state signal**
(`bank_reserves` vs `total_player_holdings`) driving graduated responses. Build
that signal once; each lever consumes it.

## Integration surface (verified)

All reuse — P2 is an assembly job. Verified file:line from the exploration pass:

- **Ledger** — `core/economy/ledger.py`. All chip moves go through `record_*`
  helpers; `LEDGER_REASONS` is a **closed frozenset** (unknown reason → silently
  dropped + logged → audit drift), so new reasons MUST be added first. Bank pool
  is virtual: `pool_depth = Σ(BANK_POOL_DEPOSIT_REASONS destructions) −
  Σ(BANK_POOL_DRAW_REASONS creations)` (`cash_mode/closed_economy.py::compute_bank_pool_reserves`).
  Counterparties are string keys: `bank()`, `player(owner_id)`, `ai(personality_id)`.
  The `house_stake_issue`/`house_stake_settle` pair is the exact buy-in/payout
  shape. **No DB-level atomicity** — write chips first, ledger is best-effort,
  drift audit (`flask_app/services/chip_ledger_audit.py::compute_audit`) is the
  backstop.
- **Bankroll** — `poker/repositories/bankroll_repository.py`
  (`load_player_bankroll`/`save_player_bankroll`; human bankroll is one row per
  `player_id`, NOT sandbox-scoped). AI helpers in `cash_mode/bankroll.py`:
  `debit_bankroll_for_seat` (regen+debit, returns None on insufficient funds),
  `credit_ai_cash_out`. Cash buy-in precedent: `cash_routes.py:1174/1387`.
  Double-settle guard precedent: `cash_sessions.ended_at` sentinel in
  `_leave_table_locked` (`cash_routes.py:4248`) — born from a real ~57.5k
  phantom-chip incident. **Idempotency is non-negotiable.**
- **Stakes** — `cash_mode/stake_settlement.py::_compute_chip_flows` is
  **entry-type agnostic** (principal/cut/match_amount/chips_at_leave only).
  House-stake override (`stake_settlement.py:164`) already does "bust → settle +
  forgive, no carry". Binding to a tournament = one nullable column
  (`tournament_entry_id`, like `table_id` was added in v111) + one lookup method.
- **Tournament hooks** —
  - Buy-in: `flask_app/routes/tournament_routes.py` `register_tournament()` after
    `registry.persist(tournament_id)` (~line 173).
  - Payout: `flask_app/handlers/tournament_game_builder.py`
    `tournament_hand_boundary()` after `_emit_tournament`, before
    `_persist_boundary` (~line 176), on `outcome.kind in (HUMAN_OUT, COMPLETE)`.
  - **Play-out gap**: `tournament_routes.py` `play_out()` runs
    `session.play_out()` WITHOUT calling the boundary hook → payout must also
    fire there.
  - Finish order: `tournament/field.py` `Elimination.finishing_position`
    (1=winner), `session.human_rank()`, `session.is_complete()`/`session.winner()`.
  - Funny-money `TournamentField.assert_conservation()` (field.py:112) fires
    throughout and does NOT see real chips — P2 keeps real chips entirely outside
    `TournamentField` and runs its own real-chip checksum.

## Two conservation statements (the contract)

1. **Funny money (unchanged):** `sum(stacks) == field_size × starting_stack` —
   enforced by `TournamentField.assert_conservation()`. P2 never touches it.
2. **Real chips (new):**
   `Σ(buy_ins) + bank_overlay == Σ(payouts) + rake`.
   For the recyclable **bank pool** specifically: `bank_overlay` is a pool *draw*
   (depletes reserves), `rake` is a pool *deposit* (fills reserves); buy-in and
   payout pass through `central_bank` but are escrow earmarked by `tournament_id`,
   not recyclable-pool flows. Verified post-event by
   `verify_tournament_conservation(tournament_id)`.

---

## Layer breakdown (mirrors P1's A/B/C — each independently committable + tested)

### Layer A — Schema v124 + ledger vocabulary
Pure plumbing; nothing calls it yet. Everything later depends on it.
Commit: `feat(tournament): P2 layer A — schema v124 + economy ledger vocab`

### Layer B — Funding policy + buy-in flow
The thermostat brain + register-time bankroll debit + pool assembly.
Commit: `feat(tournament): P2 layer B — funding thermostat + buy-in flow`

### Layer C — Prize structure + payout + idempotency
Pure payout curve + payout at the boundary AND the play-out route + the
real-chip checksum + the `payout_status` idempotency guard.
Commit: `feat(tournament): P2 layer C — prize structure + payout + idempotency`

### Layer D — Staking into entries
Bind a stake to a tournament entry; settle with the **real prize** as
`chips_at_leave`; no-carry on tournament busts.
Commit: `feat(tournament): P2 layer D — stake-into-entry binding`

---

## Layer A — Schema v124 + ledger vocabulary

### Schema (`poker/repositories/schema_manager.py`)
Follow the v123 dual-path convention (the table must exist in BOTH `_init_db`
AND a `_migrate_vNNN` method AND the `migrations` dict).

1. `SCHEMA_VERSION = 123 → 124`.
2. Add to the `tournaments` table (both `_init_db` and a migration via
   `ALTER TABLE ... ADD COLUMN`, each wrapped idempotently):

   | Column | Type | Meaning |
   |---|---|---|
   | `buy_in` | `INTEGER NOT NULL DEFAULT 0` | Per-entrant human buy-in (0 = freeroll) |
   | `rake` | `INTEGER NOT NULL DEFAULT 0` | Absolute rake skimmed to the pool |
   | `bank_overlay` | `INTEGER NOT NULL DEFAULT 0` | House contribution beyond buy-ins (the drain dial) |
   | `prize_pool` | `INTEGER NOT NULL DEFAULT 0` | `Σ buy_ins + bank_overlay − rake` (snapshot for display) |
   | `payout_status` | `TEXT NOT NULL DEFAULT 'skipped'` | `pending`→`in_progress`→`complete` \| `skipped` (freeroll/pre-economy) |

   Existing rows get `payout_status='skipped'` so the payout guard never fires on
   pre-economy tournaments. No backfill loop needed (NOT NULL + defaults).
3. `_migrate_v124_add_tournament_economy(conn)` + `migrations[124]` entry.
4. Extend `TournamentSessionRepository.save()` to write the new columns; add
   `set_payout_status(tournament_id, status)`. Thread the columns through
   `tournament_registry.persist_session()`.

### Ledger vocabulary (`core/economy/ledger.py`)
Add to `LEDGER_REASONS`:

| Reason | Flow | Pool effect |
|---|---|---|
| `tournament_buy_in` | player/ai → bank | none (escrow, earmarked by `tournament_id`) |
| `tournament_payout` | bank → player/ai | none (escrow out) |
| `tournament_overlay` | bank → (escrow) | **DRAW** (depletes reserves — the drain) |
| `ai_tournament_seed` | bank → ai | none (net-zero internal accounting) |
| `ai_tournament_return` | ai → bank | none (pairs the seed) |

Rake reuses the existing `record_table_rake` (already a
`BANK_POOL_DEPOSIT_REASONS` member). Add `tournament_overlay` to
`BANK_POOL_DRAW_REASONS`. New thin helpers mirroring `record_house_stake_issue`:
`record_tournament_buy_in`, `record_tournament_payout`,
`record_tournament_overlay`, `record_ai_tournament_seed`,
`record_ai_tournament_return` — each embeds `context={'tournament_id': ...}`.

### Tests (`tests/test_tournament/test_economy_ledger.py`)
New reasons present; each helper writes a correct row; v124 migration adds
columns with right defaults; pre-existing rows → `payout_status='skipped'`;
seed+return nets to zero; overlay registers as a pool draw / rake as a deposit.

---

## Layer B — Funding policy (the thermostat) + buy-in flow

### New module `tournament/funding.py` — the economy brain (pure)
```
compute_tournament_funding(
    *, bank_reserves: int, total_player_holdings: int,
    field_size: int, seat_price: int, human_in: bool,
) -> FundingPlan   # {seat_price, human_buy_in, ai_buy_in, bank_overlay, rake, prize_pool}
```
The **`seat_price`** is the unit the pool is denominated in — the human's buy-in,
when they opt in, equals it ("aligned to the prize pool"). Policy (v1, all
constants config-driven and tunable, **sim-tuned not guessed** — see §Sibling
levers):
- `signal = economy_state(bank_reserves, total_player_holdings)` — the **shared**
  economy-state signal (also drives the cash-rake thermostat). One function.
- **Flush**: `bank_overlay = min(bank_reserves × OVERLAY_DRAIN_PCT, OVERLAY_CAP)`,
  `rake = 0`. The bank *distributes* into the field — this is the primary
  "distribute funds" event and the only real source of an AI-only pool in v1.
- **Neutral**: `bank_overlay = 0`, `rake = 0`. Seat buy-ins only.
- **Empty**: `bank_overlay = 0`, `rake = round(gross × REFILL_RAKE_PCT)`. Refills.
- `human_buy_in = seat_price if human_in else 0` (opt-in; sit out → 0, not
  prize-eligible).
- `ai_buy_in = 0` in v1 (AI seats are bank-seeded net-zero; see below). Tourist
  *real* AI buy-ins (peer redistribution) are the deferred extension that turns
  `ai_buy_in > 0`.
- `prize_pool = human_buy_in + ai_buy_in_total + bank_overlay − rake`.

**Where the distributed chips come from (important):** with AI seats bank-seeded
net-zero, the *real* pool is `human_buy_in + bank_overlay − rake`. So an
**AI-only** tournament has a non-trivial pool **only when the bank overlays it**
(flush) — that is the bank distributing its reserves across the AI field's top
finishers. A zero-overlay, no-human tournament is just funny-money practice with
no real flow (fine — it still produces standings). Genuine *peer* redistribution
(busted AIs' real chips → finishers) is the tourist-buy-in extension.

Pure, no I/O — `bank_reserves` and `total_player_holdings` are injected (read at
the call site from `compute_bank_pool_reserves()` and a holdings aggregate).
100% unit-testable across all regimes + boundary/cap clamps.

### Config (`tournament/config.py`)
Add `buy_in: int = 0`, `payout_curve: tuple | None = None` to `TournamentConfig`
(+ `to_dict`/`from_dict`). Funding *policy* constants live in
`tournament/funding.py` (or `cash_mode/economy_flags.py` alongside the rake
knobs), not on the frozen config — the policy reads live bank state per event.

### Buy-in wiring (`tournament_routes.py::register_tournament`, after ~line 173)
1. Parse + validate `buy_in` (≥0, ≤ `MAX_BUY_IN`) from the request body.
2. Read live state: `bank_reserves = compute_bank_pool_reserves(...)`,
   `total_player_holdings = <aggregate>`.
3. `plan = compute_tournament_funding(...)`.
4. **Human affordability**: if `plan.human_buy_in > 0`, load player bankroll;
   `< buy_in` → **402** `{insufficient_funds, required, available}` before any
   chip move.
5. Debit human: `save_player_bankroll(chips − buy_in)` →
   `record_tournament_buy_in(source=player(owner_id), ...)`.
6. AI entries (v1 default): `record_ai_tournament_seed(bank→ai)` per AI seat.
7. Overlay: if `plan.bank_overlay > 0` → `record_tournament_overlay(...)` (pool
   draw). Rake: if `plan.rake > 0` → `record_table_rake(...)` (pool deposit).
8. `registry.persist(..., buy_in, rake, bank_overlay, prize_pool,
   payout_status='pending' if prize_pool>0 else 'skipped')`.
9. **Rollback**: any failure after the human debit → re-credit human +
   `registry.delete(tournament_id)` + 500. (Human debit is the only hard chip
   move; ledger rows are best-effort.)

### Tests (`tests/test_tournament/test_economy_buy_in.py`)
Funding policy across flush/neutral/empty + caps; freeroll → no debit; buy-in
debits bankroll + writes ledger; insufficient funds → 402 + no debit; rollback on
persist failure; `prize_pool`/`bank_overlay`/`rake` stored on the row; AI seed
rows created; overlay is a pool draw.

---

## Layer C — Prize structure + payout + idempotency

### Pure prize math (`tournament/economy.py`)
- `compute_payout_schedule(field_size, prize_pool, payout_curve=None) ->
  [{finishing_position, amount}]` — default curve pays top 30%, front-loaded;
  proportional within brackets; **rounding residual → 1st place** (no chip
  leakage). Default `DEFAULT_PAYOUT_CURVE` ≈ winner 38% / 2nd 24% / 3rd 15% /
  rest of top-30% share remainder.
- `payout_for_position(position, schedule) -> int` (0 if out of the money).
- `verify_tournament_conservation(tournament_id, ledger_repo) -> {balanced,
  gross, overlay, paid_out, rake, residual}` — post-event audit (scans
  `context_json.tournament_id`; **not** a hot path).

### Payout service (`flask_app/services/tournament_economy_service.py`)
Shared effectful payout so the boundary handler and the play-out route don't
diverge. Payout executes **at COMPLETE** (every finishing position is known) and
pays **all in-the-money finishers, AI and human** — that is the redistribution.
`apply_payout_on_complete(tournament_id, session)`:
1. Load tournament row; **guard**: `payout_status not in (None,'pending')` →
   return (idempotent). `prize_pool == 0` → `set_payout_status('skipped')`.
2. `set_payout_status('in_progress')` **before** any bankroll write (narrows the
   crash window — the cash double-settle lesson).
3. `schedule = compute_payout_schedule(field_size, prize_pool, curve)`.
4. Build the full finishing order: `winner = session.winner()` (position 1) +
   every `Elimination.finishing_position` from `session.field.eliminations`.
5. For each finisher whose position is **in the money**
   (`prize = payout_for_position(pos, schedule) > 0`):
   - **human** → if staked, route through `settle_stake_on_leave(chips_at_leave=
     prize)` (Layer D); else `save_player_bankroll(chips + prize)`. Then
     `record_tournament_payout(sink=player(owner_id), ...)`.
   - **AI** → `credit_ai_cash_out(pid, prize, ...)` +
     `record_tournament_payout(sink=ai(pid), ...)`.
6. `_retire_ai_entries`: `record_ai_tournament_return` for every AI seat **not**
   in the money (pairs its Layer-B net-zero seed → returns it to the bank).
7. `set_payout_status('complete')`.
Wrapped in a broad `except` that logs and leaves status `in_progress` for a
reconcile pass — must never crash the game (mirrors `_persist_boundary`).

> **`HUMAN_OUT` before `COMPLETE`:** the human's finishing position is locked at
> bust, but other finishers' positions aren't known yet — so payout does NOT fire
> on `HUMAN_OUT`. P1 already routes a busted human to the standings hub; their
> prize lands when the tournament completes (reached live, or fast-forwarded via
> play-out). One guard, all positions known, no per-finisher tracking.

### Call sites
- `tournament_game_builder.py::tournament_hand_boundary` (~line 176, after
  `_emit_tournament`, before `_persist_boundary`) — call on `COMPLETE` only.
- `tournament_routes.py::play_out` after `session.play_out()` — **closes the
  play-out gap**; same service call, idempotency guard makes a double-call safe.
- Headless `TournamentDirector.run()` completion — the AI-only path; same service
  call so an autonomous (no-human) tournament distributes correctly. This is the
  hook that makes "the tournament can happen without the human" pay out for real.

### Tests (`tests/test_tournament/test_economy_payout.py`)
Payout schedule sums to `prize_pool` with no leakage + rounding to 1st; in/out of
money; human payout credits bankroll; **double-payout blocked by the guard**;
play-out triggers payout; freeroll → `skipped`; `verify_tournament_conservation`
balanced after a full event (incl. overlay + rake).

---

## Layer D — Staking into entries

**Framing:** a wealthy backer (AI or human) stakes another player into a
tournament for a cut of their winnings. This is both a feature *and* a third
redistribution lever — idle rich chips flow into the field and return (with a cut)
on a cash. The existing stakes system already models house/AI/human stakers and
the principal/cut/carry math; binding it to a tournament entry is the whole job.

- **Schema**: add `tournament_entry_id TEXT` (nullable) + partial index to
  `stakes` (v124 if same PR, else v125). The discriminator column resolves the
  one-active-stake collision — a cash stake (keyed on `session_id`) and a
  tournament stake (keyed on `tournament_entry_id`) coexist without conflict.
- **Repo**: `StakeRepository.load_active_for_tournament_entry(entry_id)`
  (mirrors `load_active_for_session`).
- **Staker side reuses the existing offer/accept + willingness paths**
  (`cash_mode/player_staking.py`): a wealthy AI's decision to back a player, and a
  human backing an AI into a tournament, are the same flows pointed at a
  tournament entry. The backer's bankroll funds the staked seat's buy-in; the
  settlement returns principal + cut from the staked player's *real prize*.
- **No carry on tournament busts** (decided): a busted entry is terminal — there
  is no follow-on session to carry into. Reuse the house-stake override path
  (`stake_settlement.py:164`): bust → settle + forgive, no carry row. (Either set
  `format='house'` for tournament stakes, or add a `STAKE_FORMAT_TOURNAMENT`
  constant with the same no-carry branch — pick the cleaner during build.)
- **`chips_at_leave` = the real prize, not the funny-money stack** (the critical
  correctness point): settlement receives `payout_for_position(rank, schedule)`,
  then splits it staker/borrower per the untouched `_compute_chip_flows` math.
- **Tests** (`tests/test_tournament/test_economy_staking.py`): lookup by entry;
  no-carry on bust; `chips_at_leave` is the prize not the stack; staker gets
  principal + cut of prize; tournament stake coexists with a cash stake.

---

## Idempotency & error-handling (the non-negotiables)

- `payout_status`: `pending → in_progress → complete` (\| `skipped`). Written
  `in_progress` before bankroll writes, `complete` after — a crash leaves
  `in_progress` for a reconcile pass, never a silent double-pay.
- Bankroll write first, ledger row best-effort (the house pattern); a missed
  ledger row surfaces as audit drift, not a broken game.
- Rehydrate safety: `payout_status` lives on the persisted `tournaments` row, so
  a restart between register and payout knows the economy state. `register`
  debits + persists atomically-enough (debit, then persist; rollback on persist
  failure).
- The funny-money `assert_conservation` does NOT protect real chips — the
  `verify_tournament_conservation` checksum is P2's own guard; assert it in tests
  and expose it to the chip-economy admin audit.

## Open / deferred (not blocking v1)

- **Tourist real buy-ins** (drain AI bankrolls into the pool, "tourists pad
  low-tier prize pools, bust, prize cycles to winner"): a thermostat *extension*
  — make AI funding a policy knob (`ai_buy_in > 0` debits `ai_bankroll_state` via
  `debit_bankroll_for_seat`). v1 default stays bank-seeded net-zero for
  conservation simplicity.
- **Cash-table rake thermostat** (sibling lever — *build in cash mode, not P2*):
  demand-driven rake that scales by stake tier (raise the $1000 rake, switch on
  $200, then $50 only if dire) off the **shared** economy-state signal. Verify the
  current live % in `cash_mode/economy_flags.compute_rake` first. Belongs with the
  cash economy code; listed here because it shares P2's signal function and must be
  sim-modeled together.
- **Economy modeling (required before tuning constants)**: the overlay/rake/
  threshold constants and the cash-rake schedule are NOT to be guessed — model the
  closed economy as a whole and tune by **drain rate** (cf.
  `reference_cash_sim_ab_paired`, `project_casino_economy_cycling`). Stand up a sim
  that runs N tournaments at varying bank states + the cash thermostat and watches
  reserves converge. This gates flipping any thermostat *on* in prod.
- **Wealth-tax tournament variant**: entry scaled to holdings; a named variant on
  the buy-in+overlay base. Funding policy already centralizes the knob.
- **Frontend**: buy-in confirm + prize-pool / payout-structure display + a
  "results / in-the-money" surface. The standings hub (P1) is the natural home.
- **Career carry-out** (relationship/prestige deltas from tournament results):
  P4 territory.

## Files

**New**: `tournament/funding.py`, `tournament/economy.py`,
`flask_app/services/tournament_economy_service.py`,
`tests/test_tournament/test_economy_{ledger,buy_in,payout,staking}.py`.

**Modified**: `poker/repositories/schema_manager.py` (v124 + stakes column),
`core/economy/ledger.py` (reasons + helpers), `tournament/config.py`,
`flask_app/routes/tournament_routes.py` (register buy-in + play-out payout),
`flask_app/handlers/tournament_game_builder.py` (boundary payout call),
`flask_app/services/tournament_registry.py` (persist economy columns),
`poker/repositories/tournament_session_repository.py` (save columns +
`set_payout_status`), `poker/repositories/stake_repository.py`
(`load_active_for_tournament_entry`), `cash_mode/stakes.py` (tournament stake
format).

## Build order
A → B → C → D, each its own commit + green tests, then the full
`tests/test_tournament/` suite + the live-play integration (the P1 cadence).
