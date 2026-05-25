---
purpose: Replace the ephemeral-tourist/clone fish mechanisms with permanent, pool-funded-bankroll fish personas at casinos
type: spec
created: 2026-05-24
last_updated: 2026-05-24
---

> **Committed 2026-05-24** as merge `566d043f` (parents `f33f08cf` phase-1 + `665b72e7` origin/development). 29 files. `test_cash_mode` + `test_memory` green.

# Cash Mode: Fish Are Permanent Personalities

## Decision

Discard **both** prior ephemeral mechanisms:
- phase-1's synthetic `tourist-<uuid>` pids + inline `ephemeral_personality` seat blob + `tourist_factory.py` + `tourist_avatars.py` + 73 generated personalities.
- development's `spawn_ephemeral_fish` clone-into-DB-rows + delete-at-teardown (source of its ~-77K audit drift).

Fish are **9 curated, permanent personalities** already in `personalities.json`
(`archetype:'fish'`, `rule_strategy:'fish'`, real stable pids: `vacation_greg`,
`bachelorette_brenda`, `cruise_carl`, `birthday_bobby`, `after_hours_trent`,
`lucky_mona`, `slots_linda`, `golf_trip_brad`, `freddie_fratboy`).

## Two tags drive everything

| Tag | Governs |
|---|---|
| `archetype=='fish'` (on persona + stamped on seat via `ai_slot_fish`) | casino selection, lobby-exclusion, **relationship-skip**, casino-binding in movement, pool-funded bankroll |
| `table_type=='casino'` | venue only — spawn / teardown / closing-countdown |

## Funding model: pool-funded bankroll (NOT seat-seed)

Fish get a **real bankroll, seeded from the bank pool**, then use the *normal*
buy-in path. This dissolves the "winnings leak into bankroll" problem (it becomes
the intended flow) and lets fish inherit the existing rebuy/go-home machinery.

- **Prefund (spawn/refill):** `prefund = int(table_max_buy_in * rng.uniform(2.5, 3.6))`
  (≈3× jittered; local `rng`). Whale = same path, `rng.uniform(10, 18)×`, triggered
  when pool exceeds a high-water mark (**deferred** — build the `_fish_prefund(..., whale=False)`
  seam now, wire the trigger later).
  - Move pool→bankroll **without minting**: `bankroll_repo.save_ai_bankroll(state, sandbox_id=...)`
    **with no `chip_ledger_repo`** (no `ai_seed`), paired with
    `record_casino_seat_seed(personality_id=pid, amount=draw)` for the pool draw.
    Draw the **delta** (`prefund - existing_bankroll`) defensively; invariant says
    existing is 0.
- **Seat:** normal `debit_bankroll_for_seat(pid, buy_in)` + place `ai_slot_fish(pid, buy_in)`.
  Prefund ≥ buy_in guarantees the `debit_bankroll_for_seat` clamp-leak never fires.
- **Re-buy / go-home:** INHERITED from `movement.py`'s existing `decide_leave_or_rebuy`
  / `REBUY_*` / `forced_leave`. No new code.
- **Movement = constrain, not skip:** run fish through movement but suppress
  `stake_up` and `bored_move` (keep them casino-bound); allow `stay`/`rebuy`/`take_break`/`forced_leave`.

## Conservation invariant (load-bearing)

> **A fish's bankroll is 0 whenever it is not seated at a casino.**

Therefore drain bankroll→pool on **every** casino exit, not just teardown:
- fish `forced_leave`/`take_break`/bust → `_drain_fish_to_pool` (record `casino_seat_return`, zero bankroll, abort-on-stranded discipline).
- table teardown → cash out seat→bankroll, then drain bankroll→pool for every seated fish.
- fish are **excluded from autonomous regen** (regen would mint into a pool-backed bankroll).

Loop: vice → pool → fish bankroll → seat → (grinders extract into their own bankrolls) → remainder drains back to pool. Closed.

## Build sequence

1. **Data:** add fixed `fish_leak` to each of the 9 personas (values from `FishLeak` enum). ✅ `list_fish_for_cash_mode()` added; ✅ `ai_slot_fish()` added.
2. ✅ Re-key `_casino_has_seated_fish` + `_return_seat_residuals_to_pool` filters → `archetype=='fish'`. ✅ `closed_economy.py` constants resolved.
3. **Resolver** (`resolve_casino_provisioning`): resolve the merge conflict to dev's 3-pass lifecycle + persona selection (`list_fish_for_cash_mode`), `ai_slot_fish`, prefund-from-pool, drain-on-teardown (NO row deletion). Rewrite `_refill_one_fish` (persona pick + prefund + buy-in). Drop `CASINO_FISH_PER_TABLE` (use `rng.randint(MIN,MAX)`).
4. **`closed_economy.py` vice loop:** swap `load_fish_ids` → `list_fish_for_cash_mode` (thread `personality_repo`).
5. **Drain-on-exit hook** in movement/refresh: when a fish seat empties, drain its bankroll→pool.
6. **Movement constraint:** fish → suppress `stake_up`/`bored_move`; remove the old `ephemeral_personality` skip.
7. **`cash_routes.py`:** remove inline `ephemeral_personality` seat/controller/avatar branches (fish route through normal DB lookup); per-pair relationship suppression.
8. **Per-pair suppression** in `memory_manager.on_hand_complete` → `dispatch_events`: skip any pair where observer or target ∈ fish_ids (via `set_fish_ids`).
9. **Exclude fish from regen.**
10. **Delete** `tourist_factory.py`, `tourist_avatars.py`, `ai_slot_ephemeral`, `personality_for_seat` inline branch.
11. **Tests:** reconcile combined `test_casino_provisioning.py`; rewrite tourist/cold-start tests for the persona model; delete `test_tourist_*`.

## Status (2026-05-24): COMPLETE on the uncommitted merge

All 11 build steps landed. Deltas from the plan as written:
- **Step 4 (vice loop) NOT needed:** `load_fish_ids` walks `ai_bankroll_state` by archetype and still catches seated fish (they have pool-funded rows); an un-seated fish can't be a vice candidate. So Risk 3 doesn't apply to this model — left as-is.
- **Step 5 (drain-on-exit) implemented as a resolver sweep, not a movement hook:** the top of `resolve_casino_provisioning` drains any fish not currently seated but holding chips. Catches every exit mode (go-home/bust/teardown) in one place.
- **Conservation fix found in testing:** `_prefund_fish_from_pool` now **caps the draw at current pool depth** (was over-drawing when pool < the ~3× target, pushing the pool negative).
- **fish_leak wiring:** added `bankroll_repo.load_fish_leak` and threaded it through `full_sim._build_controller` (the sim path didn't pass it; cash_routes already read it from the persona config).

### Validation
- `test_cash_mode` + `test_memory` green (reconciled 5 prefund-model assertions; added `TestFishSeats`; fixed a self-inflicted bug where an Edit displaced `set_relationship_repo`'s `self._table_max_buy_in =` into `set_fish_ids`).
- 300-tick sims: casinos spawn ($2+$10), fish lifecycle works, **fish chip accounting clean** (held ≈ drawn − losses-to-grinders).

## Open / deferred
- **Whale trigger** (pool high-water → roll). The `_fish_prefund(whale=...)` seam is wired; the trigger is not.
- **Pre-existing `vice_spending` audit drift** (~−1.5k–−5.6k/300 ticks, RNG-dependent). Isolated via a no-casino control (which drifted MORE, −3600) + clean fish accounting → it is NOT this work. Trace separately with per-tick (`audit_every=1`) correlation.
- Stale `is_ephemeral_tourist` variable name in `game_handler.py` (functions correctly via `fish_leak` presence).
- Cold-start depends on the 9 fish being in the `personalities` **table** (docker-entrypoint `seed_personalities.py` handles it; confirmed present in the dev DB).
