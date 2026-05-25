---
purpose: Replace the $200 casino with a rare pool-funded whale that sits at the $200 cardroom (lobby) table, drawing grinders to farm it — the top relief gate of the bank-pool dam
type: spec
created: 2026-05-25
last_updated: 2026-05-25
---

# Cash Mode: Whale at the Cardroom

## Decision

The high-stakes relief valve should be a **whale at a real cardroom
(lobby) table**, not a dedicated `$200` casino. A casino is a synthetic
venue we assemble (fish + pulled-in predators); a whale is a high roller
who sits down *where players already are*. It's the organic, dramatic
version, and a much bigger gate: the dormant whale prefund is **10–18×
the max buy-in** (vs a casino fish's 2.5–3.6×), so a `$200` whale is a
**~200k–360k single draw** from the pool — the right "open the big gate
when the reservoir is bloated" release.

**Retire the `$200` casino tier.** Casinos cap at `$50` (steady fish
farming for grinders); the `$200`+ band is whale-only.

This is the activation of the long-dormant whale code
(`_fish_prefund(whale=True)`) and realizes the "whale = pool relief
valve" idea from `CASH_MODE_TABLE_ATTRACTIVENESS.md` (component 4).

## What this leans on (already shipped, 2026-05-25)

- **Fish movement** — a whale is `archetype='fish'`, so
  `cash_mode/movement.py:_coerce_fish_movement` already governs it:
  stay-and-reload-until-bust, storm off when tilted. No new movement
  logic for the whale itself.
- **Predator retention** — `_coerce_predator_retention` fires on
  `table_has_fish`, which is computed from the `archetype='fish'` stamp
  on *any* table (it's just non-trivial at casinos today because fish are
  casino-only). A whale at a lobby table makes `table_has_fish=True`
  there, so grinders already at that table **stay and farm it** with zero
  new code. Fatigue rotation (`CASINO_PREDATOR_FATIGUE_FLOOR`) applies too.
- **Pool dam** — `casino_provisioning.py` already has the laddered
  open + pool-floor wind-down pattern and the conservation-safe pool↔seat
  helpers (`_prefund_fish_from_pool`, `record_casino_seat_seed`,
  `record_casino_seat_return`, `_drain_fish_bankroll_to_pool`,
  drain-on-exit sweep). Reuse these for the whale.

So the genuinely new work is: (1) seat a fish-class entity at a *lobby*
table safely, (2) a pool-depth whale trigger, (3) the cross-table pull.

## Phases

### Phase 1 — Retire the $200 casino tier (trivial)
- Drop `'$200'` from `CASINO_SPAWN_THRESHOLDS` and
  `CASINO_CLOSE_THRESHOLDS` (`cash_mode/casino_provisioning.py`).
  Casinos now ladder `$2 → $10 → $50`.
- Update `tests/test_cash_mode/test_casino_provisioning.py` (the dam
  tests build a `$200` casino — repoint them to `$50` or to a synthetic
  tier, or convert to whale tests).
- The `$200` tier comment + the dam-ladder/wind-down logic stay; they
  just no longer have a `$200` entry.

### Phase 2 — Whale spawn at the $200 cardroom (the core)
A new resolver pass (in `casino_provisioning.py` alongside the casino
passes, or a sibling `whale_provisioning.py`), run from
`refresh_unseated_tables`:

1. **Trigger (dam, top gate).** Add `WHALE_POOL_THRESHOLD` — a high
   watermark *above* the `$50` casino threshold (the whale is the biggest
   release). Spawn a whale only when:
   - bank pool reserves ≥ `WHALE_POOL_THRESHOLD` (covers the ~360k max
     prefund + buffer), AND
   - no whale already live (one whale at a time — it's a rare event), AND
   - a `$200` lobby table exists with ≥1 open seat.
   Consider a low-watermark wind-down mirror (whale leaves / isn't
   replaced once the pool normalizes), like the casino dam.
2. **Fund + seat.** Pick a fish persona not already seated; prefund it
   deep from the pool: `_prefund_fish_from_pool(..., target_chips=
   _fish_prefund(max_buy_in, rng, whale=True))`. Seat it at the `$200`
   lobby table via `ai_slot_fish(pid, buy_in)` — consider a distinct
   `archetype='whale'` stamp (see "Open questions") or reuse
   `'fish'`. Debit the buy-in from bankroll (`debit_bankroll_for_seat`),
   exactly like casino spawn (Pass 3 is the template).
3. **Conservation.** Same invariants as casino fish: pool → whale
   bankroll (seed) → seat buy-in; on exit, seat residual + bankroll
   return to pool (`record_casino_seat_return` /
   `_drain_fish_bankroll_to_pool`). The whale is just a very deep fish.
4. **Ticker.** Emit a `world_event` ("🐋 a high roller just sat down at
   $200") via the realtime ticker (`flask_app/services/ticker_service.py`
   / the `lobby_tick`/`world_event` socket push). Flavor *and* a pull
   signal.

### Phase 3 — Cross-table predator pull (so the whale gets farmed)
High-stakes *cardroom* tables are sparse, so a whale needs grinders to
*show up* — retention only keeps ones already there. Add the attraction:
- When a whale (or any fish) is at a table, idle/seated-elsewhere
  grinders who can afford the stake are **drawn to seat there**
  preferentially. Simplest hook: in the lobby live-fill candidate
  ordering (`cash_mode/lobby.py`, the per-table fill), boost the priority
  of seating affordable predators at a table with a whale — analogous to
  the existing hungry-grinder reorder (`lobby.py:1055-1079`) but keyed on
  "this table has a whale" rather than "this is a casino."
- This is a thin slice of the deferred attractiveness *pull* surface;
  full stake_fit/crowd scoring is still out of scope.

## Risks / investigation items (do these first in the new context)

1. **Lobby assumes no fish.** Today fish are casino-only and the lobby
   idle-pool filter excludes them (`lobby.py:616-622`). A *seated* whale
   isn't in the idle pool, so that filter is fine — but **audit for any
   code that evicts/cleans fish from lobby tables or assumes lobby seats
   are never `archetype='fish'`** (chip-ledger audit, lobby refresh,
   seat-conservation paths). This is the ghost-seat/fish-accounting bug
   class — treat carefully (see `feedback_cash_seat_double_seat_recurrence`,
   `project_casino_fish_as_personas`).
2. **Capital.** A `$200` whale draws ~200k–360k from the pool at once.
   Confirm `WHALE_POOL_THRESHOLD` leaves a healthy floor and the whale
   can't bankrupt the pool. One whale at a time.
3. **Conservation.** Run the audit (`sim_runner` audit / chip-ledger
   audit) before/after a whale's full life-cycle (spawn → farmed → bust →
   return) and confirm zero drift.
4. **Does the $200 cardroom have predators at all?** If consistently
   empty, Phase 3's pull is load-bearing, not optional.

## Validation

1. **Unit**: whale spawns only when pool ≥ threshold + a `$200` lobby
   seat is open; whale is funded deep (10–18×) and seated; conservation
   (seed = pool draw; return on exit). Predator-pull ordering test.
2. **Sim** (`scripts/sim_experiments/fish_money_flow.py`, extend to show
   lobby-table composition): a whale appears at `$200` cardroom, grinders
   are pulled in and farm it, `fish_net_to_players` jumps, the whale's
   deep stack drains into predator bankrolls, audit drift flat.
3. **Compare**: net transfer + pool-drain vs the retired `$200` casino
   (was ~+9.8k/100 ticks). Whale should drain *more per event* but rarer.

## Open questions

- **`archetype='whale'` vs `'fish'`?** A distinct stamp lets the UI/
  ticker/movement treat whales specially (bigger storm-off drama, no
  shed, distinct display) but means auditing every `archetype=='fish'`
  check to decide if it should include whales. Reusing `'fish'` is
  cheaper and inherits all fish handling. Lean: reuse `'fish'` + a
  `whale: true` seat flag for display/ticker only.
- **Whale at `$200` only, or any high tier ($200/$1000)?** Start `$200`.
- **Wind-down**: does an un-farmed whale eventually leave (pool recovers)
  or only bust? Mirror the casino dam low-watermark, or let it ride.
- **Keep `$1000`?** Out of scope here; no casino or whale there yet.

## Status of related work
- Casinos ($2/$10/$50) + predator pull/retention + fatigue rotation +
  dam (ladder + wind-down): SHIPPED (see
  `project_table_attractiveness` memory; commit `33fb1ed4` and prior).
- This doc: the `$200`+ whale replacement for the (to-be-retired) `$200`
  casino tier.
