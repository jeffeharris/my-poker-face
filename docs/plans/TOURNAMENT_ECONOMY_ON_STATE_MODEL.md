---
purpose: Pin the multi-table tournament economy (P2) to the cash-mode state model — shared ledger, owner taxonomy, custody parcels, and one economy-signal "chairman" — so P2 builds on the unified substrate instead of a parallel system
type: design
created: 2026-05-30
last_updated: 2026-05-30
status: DRAFT — alignment note; supersedes the standalone framing in MULTI_TABLE_TOURNAMENT_P2_ECONOMY.md where they disagree
---

# Tournament Economy on the Cash State Model

## Why this note exists

Two economy efforts are in flight on different branches:

- **`development` — `CASH_MODE_STATE_MODEL.md`**: replaces cash-mode reconcilers
  with two enforced state machines (Presence, Chip-custody) over a **unified
  human+AI ledger** with an explicit **owner taxonomy**, governed by stated
  invariants (I1 conservation … I6 idempotent terminal transitions). Cut 2 has
  shipped (`record_transfer` + a `seat:<game_id>` counterparty + `TRANSFER_REASONS`).
- **`tournaments` — `MULTI_TABLE_TOURNAMENT_P2_ECONOMY.md`**: the tournament
  buy-in / overlay / payout / staking thermostat, written standalone.

They are the **same substrate** seen from two angles. If P2 ships its own ledger
vocabulary, idempotency scheme, and bank-signal in isolation, we will have built
exactly the kind of second authority the state model exists to delete. This note
re-frames P2 as the **tournament-shaped instance** of the cash state model's
machines, and names the one new shared piece both economies need: the
**economy-signal chairman**.

> Precedence: where this note and `MULTI_TABLE_TOURNAMENT_P2_ECONOMY.md` conflict,
> this note wins (it post-dates it and reflects the `development` substrate). The
> P2 doc's *layer breakdown, payout curve, funding regimes, and file:line
> integration surface remain valid* — only the ledger/idempotency/signal framing
> changes.

## The mapping (P2 concepts → state-model concepts)

| P2 concept | State-model concept | Consequence |
|---|---|---|
| `tournament_buy_in`, `tournament_payout` | **Transfers** (`record_transfer`, no `central_bank` side) between `player:<id>`/`ai:<id>` and a new `tournament:<id>` (or `entry:<id>`) escrow counterparty | Drift-invisible escrow earmarked by tournament; sibling of the shipped `seat:<game_id>` |
| `tournament_overlay` | **Bank pool DRAW** (creation toward the field) | Stays a `central_bank` creation reason — counts in drift math (it really moves reserves) |
| `rake` / wealth-tax | **Bank pool DEPOSIT** (`table_rake`/`rake_sink`) | Reuses the existing recyclable deposit reason; refills reserves |
| A tournament entry's chips | A **chip-custody parcel** at a non-bankroll location | `IN_BANKROLL → COMMITTED (tournament escrow) → … → IN_BANKROLL (payout)` — a sibling of `AT_TABLE` |
| `payout_status: pending→in_progress→complete` | **I6 idempotent terminal transition** | Don't invent a bespoke guard — generalise the `ended_at IS NULL` pattern the session machine uses |
| `compute_tournament_funding(...)` | A **policy over the ledger read-model** | Reads `bank_reserves`/`holdings` as *derived* values (single authority, I4), not bespoke aggregates |

## The owner taxonomy gets one (maybe two) new entries

The state model's I1 taxonomy is `player_bankroll, ai_bankroll, table_stack, pot,
bank/house_pool, rake_sink, loan/stake_escrow, stake_obligation`. Tournaments add:

- **`tournament:<id>`** (escrow) — a transfer-only counterparty, exactly like the
  shipped `seat:<game_id>`: buy-ins flow `player/ai → tournament:<id>`, payouts
  flow `tournament:<id> → player/ai`. Drift-invisible (earmarked, not minted).
- Overlay and rake are **not** new owners — overlay is a `central_bank` draw, rake
  a `central_bank` deposit, both already modelled.

So P2 Layer A is no longer "add 5 reasons + a parallel conservation checksum." It
is: **add `tournament:<id>` to the taxonomy, add the buy-in/payout transfer reasons
to `TRANSFER_REASONS`, and reuse `record_transfer` / overlay-draw / rake-deposit.**
The "two conservation statements" in the P2 doc collapse into the state model's
single I1 — funny-money conservation (`TournamentField.assert_conservation`) stays
exactly as is and orthogonal; real-chip conservation is just I1 over the unified
ledger, no separate `verify_tournament_conservation` machinery required (it becomes
a query, not a subsystem).

## The economy-signal chairman (the one genuinely new shared piece)

Both thermostats — the tournament overlay/rake dial **and** the cash-table rake
dial — want the same input. The state model makes it a clean derived read (bank
reserves and holdings become ledger-authoritative under D2/D3), so build it once:

```
core/economy/economy_signal.py  (or cash_mode/economy_chairman.py)

  signal(ledger_repo, *, sandbox_id) -> EconomyState
      reserves   = compute_bank_pool_reserves(ledger_repo, sandbox_id=…)   # existing
      holdings   = total_player_holdings(ledger_repo, sandbox_id=…)         # ledger-derived
      ratio      = reserves / max(1, holdings)
      regime     = FLUSH | NEUTRAL | EMPTY     # around the ~0.08 setpoint (EXP_006)
      returns EconomyState(reserves, holdings, ratio, regime)

  # Pure policy functions, each lever consumes the same EconomyState:
  tournament_funding(state, *, field_size, seat_price, human_in) -> FundingPlan
  cash_rake_schedule(state) -> RakeSchedule        # raise $1000, switch on $200, …
```

Properties (inherited from the state model's discipline):

- **Pure + caller-locked.** Like the Presence/Custody machines, these are pure
  functions; the caller holds `get_sandbox_lock` across read-signal → decide →
  apply-transfers so the decision and its ledger writes commit atomically (§6.1 of
  the state model).
- **One authority (I4).** `reserves`/`holdings` are *derived* from the ledger, not
  separately stored — no second number to drift. This is exactly why the cash
  model makes bankroll ledger-derived; the chairman is the first consumer that
  benefits across both economies.
- **Setpoint from sim, not guess.** EXP_006 validated a proportional-overlay
  controller parking reserves at ~0.08, conservation-clean. The chairman encodes
  that control law; constants stay sim-tuned (`reference_cash_sim_ab_paired`).

"Take from the bank vs take from the rich," resolved cleanly: **overlay** is a
`bank → tournament:<id>` draw (take from the bank); **rake / wealth-tax / tourist
buy-ins / staking** are transfers from the field into the escrow or `rake_sink`
(take from the rich). The chairman sets the rates off the one signal; the levers
are just which transfer reasons fire.

## Revised build order (replaces P2 §"Build order")

Gate everything on the state model's **Phase 0 (unified ledger substrate)** landing
on `development`. Then, on a tournaments branch rebased onto it:

0. **(state model, on `development`)** Unified human+AI ledger + owner taxonomy +
   `record_transfer`. *Already partly shipped (Cut 2).* Tournament work consumes it.
1. **Economy-signal chairman** — `economy_signal.py` + `tournament_funding` +
   `cash_rake_schedule`, pure, over the ledger read-model. Unit-tested across
   flush/neutral/empty + the EXP_006 setpoint. (This is P2 Layer B's "brain",
   promoted to shared and moved earlier.)
2. **Tournament escrow custody** — `tournament:<id>` counterparty +
   buy-in/payout via `record_transfer`; overlay-draw / rake-deposit. (P2 Layer A,
   now a thin extension of the shipped ledger rather than a parallel one.)
3. **Buy-in flow** at `register_tournament` — affordability (402), debit, escrow,
   overlay/rake, rollback. (P2 Layer B, unchanged surface.)
4. **Payout** as an **I6 terminal transition** at COMPLETE — boundary +
   play-out + headless director, `payout_status` guard generalised from the
   session machine's terminal-idempotency pattern. (P2 Layer C.)
5. **Staking into entries** — reuse the stake machine (state model Tier 2) bound to
   `tournament:<id>` instead of a fresh subsystem. (P2 Layer D.)

## What stays exactly as the P2 doc says

- The **funding regimes** (flush overlay / neutral / empty rake) and the
  **proportional-overlay control law** validated by EXP_006.
- The **payout curve** (top ~30%, front-loaded, paid to all ITM finishers — the
  redistribution mechanism), and that the tournament is **autonomous** (runs with
  or without the human).
- The **file:line integration surface** (`register_tournament`, the boundary
  payout call, the play-out gap, finish order) — those hooks are unchanged.
- Funny-money `TournamentField.assert_conservation()` — untouched, orthogonal.

## Open coordination items

- **Schema divergence.** `development` is at v127 (dossier B1 = v125); the
  `tournaments` branch is at v124 (the tracker-drop migration). On merge, renumber
  the tournament migrations above `development`'s head — same collision class as the
  circulating-flag v123 incident. P2's "v124" assumption is stale.
- **Same file, two efforts** (`core/economy/ledger.py`). Land the chairman + the
  `tournament:<id>` reasons *after* the state model's ledger substrate is on
  `development`, not in parallel, to avoid a reconcile.
- **Sign-off carried over:** the state model's pivotal table-as-projection decision
  (its §6) is accepted (D1) but unbuilt; the tournament economy does not depend on
  it (tournaments have no `cash_tables` seat map), so P2 can proceed on Phase 0 +
  the chairman without waiting for cash Phase 3.
