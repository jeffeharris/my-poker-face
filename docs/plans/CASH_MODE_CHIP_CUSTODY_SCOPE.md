---
purpose: Scope for building the chip-custody state machine — the second of the two state machines in CASH_MODE_STATE_MODEL.md (§5.2), making chip-loss structurally impossible and bankroll ledger-derived
type: design
created: 2026-06-01
last_updated: 2026-06-01
---

# Cash Mode — Chip-Custody Machine (scope)

The second machine from `CASH_MODE_STATE_MODEL.md §5.2`. The **Presence** machine
(§5.1) is built + flipped on dev; this is the unbuilt twin. It answers "**what
state is this money in, and who owns it?**" and makes the chip-forfeiture bug
class *structurally impossible*, the way Presence did for ghost/double seats.

This is a SCOPE, not a blueprint — it's grounded in the real chip code (verified
2026-06-01), states the key design decision, the gaps, the chokepoints, a phased
plan that reuses the Presence cutover machinery, and an honest cost/priority read.

## Why (and why it's not urgent)

The original loss ($2k/$8k swept by the reaper) is already **fixed behaviourally**
(Cut 1 freeze-guard) and **auditable** (Cut 2 human statement). So the custody
machine is **defense-in-depth + design completion**, not an open bug:
- Cut 1 makes the reaper bug *not happen* (a guard that can regress).
- The custody machine makes it *unrepresentable*: the only exit from `AT_TABLE`
  is `→ SETTLING → IN_BANKROLL`; a janitor can *trigger* settlement, never skip
  it. Plus it delivers **D2** (bankroll = sum of ledger parcels, not a bare
  mutable int — closing the "bankroll is an unauditable integer" gap).

## What already exists (grounded, not the design's imagined shape)

| Piece | Reality today |
|---|---|
| Ledger | `core/economy/ledger.py` — entity accounts `player(owner_id)`, `ai(pid)`, `seat(game_id)`, `bank()`; `record()` (central-bank creations/destructions) + `record_transfer()` (pure entity↔entity, `TRANSFER_REASONS`). |
| Human buy-in / cash-out | **Ledgered (Cut 2):** `record_player_buy_in` = transfer `player→seat(game_id)`; `record_player_cash_out` = `seat→player`. Wired at the 3 human sit/rebuy/leave sites in `cash_routes.py` (647/1536/4839). So a human's **at-table chips ≈ the `seat(game_id)` account balance** — the custody substrate is already half-here for humans. |
| AI buy-in / cash-out | **NOT ledgered as seat transfers.** AI chips at a table live in the seat stack + the bankroll int; conservation is reconciled by the audit counting live seat stacks, not by `player↔seat` rows. So custody-via-ledger does not yet cover AI. |
| Bankroll | **Bare mutable int** — `PlayerBankrollState.chips` / `AIBankrollState.chips` (`cash_mode/bankroll.py`). NOT derived from the ledger (no `balance_of`/derive path exists). **D2 is unbuilt.** |
| Reaper | Cut 1 freeze-guard in `_boot_sweep_stale_cash_rows` (`cash_mode/lobby.py`) — behavioural skip of active/paused sessions. No structural `SETTLING`-only exit. |

## The key scoping decision: custody as a LEDGER PROJECTION (not a new state table)

The design (§5.2) drew a per-parcel state machine (`IN_BANKROLL → COMMITTED_TO_SEAT
→ AT_TABLE → SETTLING`). The grounded insight — the analog of Presence's
"table-as-projection" — is that **the custody STATE is largely derivable from the
ledger**, so we should NOT build a parallel per-parcel state store:
- `AT_TABLE` amount for an entity = its `seat(game_id)` account balance.
- `IN_BANKROLL` = the entity's net ledger position outside any seat.
- `SETTLING` is the transient during the seat→bankroll transfer.

So the machine is mostly: **(a)** make bankroll a *derived* read over the ledger
(D2), **(b)** route AI chips through the same `player/ai ↔ seat(game_id)` transfers
so the ledger is the single custody record for both, and **(c)** enforce the one
structural invariant — *a seat balance can only return to a bankroll via a
settlement transfer; nothing may zero a non-empty `seat()` balance* — at the
reaper / leave / bust chokepoints. A thin explicit `custody_state` column is
optional sugar; the ledger is the authority.

## Gaps to close (the actual build)

1. **Structural `AT_TABLE`→`SETTLING`-only exit.** The invariant: a non-empty
   `seat(game_id)` balance is only retired by a `seat→player/ai` settlement
   transfer (cash-out / bust-settlement). The reaper must *settle* (move the seat
   balance back to bankroll) before deleting a row — never zero it. Today Cut 1
   skips active sessions; the machine would make "delete a row whose seat balance
   is non-zero" impossible (assert/guard at the repo or a CHECK-like invariant).
2. **AI parity.** Wire AI sit/leave/bust through `seat(game_id)` transfers (or an
   `ai↔seat` equivalent) so AI chips have the same ledger custody as humans.
   Today only humans do. This is the biggest net-new wiring.
3. **D2 — ledger-derived bankroll.** Add a `balance_of(account)` read; make
   bankroll reads derive from the ledger (keep the int as a cache). Audit every
   reader/writer of `PlayerBankrollState.chips` / `AIBankrollState.chips`.
4. **Parcel granularity.** Decide: track custody per *entity-at-seat* (one
   `seat(game_id)` balance — simplest, matches today) vs per *buy-in parcel*
   (finer, needed only if partial settlements / split pots across buy-ins must be
   distinguished). Recommend entity-at-seat to start.

## Chokepoints to wire (grounded)

| Event | Where | Custody transition |
|---|---|---|
| Sit / buy-in | `cash_routes.py` sit/sponsor (+ AI sit in lobby/casino) | `IN_BANKROLL → seat()` (humans done; **AI new**) |
| Rebuy / top-up | `cash_routes.py` rebuy/topup + `_increment_cash_session_buy_in` | additional `IN_BANKROLL → seat()` |
| Hand award | `cash_mode/full_sim.play_one_hand` (sim) + live award path | chips move *between* `seat()` balances at one table (intra-table; may stay implicit if the table conserves) |
| Leave / cash-out | `cash_routes.py` leave (4839) + AI leave (movement) | `seat() → IN_BANKROLL` settlement (humans done; **AI new**) |
| Bust | leave path with 0 take-home | seat balance → 0 via loss transfers, not a silent drop |
| Reaper | `_boot_sweep_stale_cash_rows` | **must settle the seat balance back to bankroll before delete** — the structural guarantee |
| Rake / vice / hustle | award + off-grid paths | already ledgered via `record()` (central_bank side) — interacts, must stay conservation-clean |

## Phasing — reuse the Presence cutover machinery

The Presence cutover's tooling is directly reusable (entity-agnostic by design):
1. **Substrate** — add `balance_of` + an env-gated flag `CHIP_CUSTODY_ENABLED`
   (mirror `PRESENCE_AUTHORITY_ENABLED`), default OFF.
2. **Shadow / dual-write** — wire AI `seat()` transfers alongside the existing
   stack writes (conservation-neutral), and a divergence audit comparing
   ledger-derived bankroll vs the stored int (mirror
   `scripts/validate_presence_shadow.py` + `audit_presence_divergence.py`).
3. **Backfill** — reconstruct `seat()` balances + bankroll-from-ledger for
   existing sandboxes (mirror `scripts/backfill_presence.py`); the ledger history
   already exists for humans.
4. **Authority flip** — bankroll reads derive from the ledger; the reaper settles-
   before-delete; validate in sims + a dev soak, then prod.

## Relationship to Presence (they're coupled, not independent)

`SEATED` (Presence) ↔ `AT_TABLE` (custody) are the same physical event from two
angles; `SIT`/`LEAVE` should drive *both* a presence transition and a custody
transfer in the same `get_sandbox_lock` critical section (design §6.1). The
`save_table` chokepoint that now drives presence is the natural place to also
drive the seat-balance custody transfer — so custody can ride the same chokepoint
+ lock + audit harness. This is the strongest argument for doing it *after*
Presence→prod (reuse a proven, deployed seam) rather than before.

## Risks / open questions

1. **AI conservation model differs** — AI chips are audit-reconciled via seat
   stacks, not ledgered. Routing AI through `seat()` transfers is the bulk of the
   work and the main drift risk; needs the paired-probe sim A/B (memory
   `reference_cash_sim_ab_paired`) to prove conservation.
2. **Bankroll-as-int has many readers** — making it derived (D2) touches every
   read of `.chips`. Keep the int as a cache initially; flip reads incrementally.
3. **Sim hot-path cost** — per-hand award would add ledger writes; batch/derive
   like the Presence sim guidance, don't pay per-transition SQLite on the ticker.
4. **Rake / vice / hustle** already ledgered via `central_bank`; the custody
   transfers must compose with them without double-counting (the pre-existing
   ~16/2.27M vice/seed rounding drift lives here — diagnose before, not during).

## Cost & recommendation

**Sizeable** — bigger than any single Presence cut, mostly because of AI parity
(2) + D2's read-surface (3). It is **defense-in-depth**, not a live bug.

Recommended sequence: **(1)** Presence → prod (real user-facing payoff, already
built); **(2)** Presence read-side polish if desired; **(3)** then the custody
machine, riding the proven `save_table` chokepoint + lock + audit/backfill
harness. Building it before Presence→prod would stack two big in-flight cutovers
on the same seam. If we do want a cheap down-payment sooner, the highest-value
slice is the **reaper settle-before-delete** structural guard (turns Cut 1's
behavioural guard into a structural one) — small, and it's the exact bug that
started all this.
