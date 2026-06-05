---
purpose: Pin the multi-table tournament economy (P2) to the cash-mode state model — shared ledger, owner taxonomy, custody parcels, and one economy-signal "chairman" — so P2 builds on the unified substrate instead of a parallel system
type: design
created: 2026-05-30
last_updated: 2026-05-31
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

## Substrate status — the chip-custody machine LANDED on `development` (2026-06-01)

The dependency this note was gated on is **built and cut over on dev** (Presence +
chip-custody machines, schema v129). Verified in `core/economy/ledger.py` +
`poker/repositories/chip_ledger_repository.py`:

- **Accounts** `bank()`, `player(owner_id)`, `ai(pid)`, `seat(game_id)`; `record()`
  (central-bank creations/destructions) + `record_transfer()` (entity↔entity,
  `TRANSFER_REASONS`).
- **Buy-in/cash-out as transfers — humans AND AI:** `record_player_buy_in/cash_out`
  *and* `record_ai_buy_in/cash_out` (`player/ai → seat(game_id)` and back). AI parity —
  the thing the foundation audit was gating on — landed.
- **D2 ledger-derived bankroll:** `chip_ledger_repository.balance_of(account)` exists;
  bankroll derives from the ledger (int kept as a cache). The chairman's
  reserves/holdings are now genuinely *derived* (single authority, I4), not a bespoke
  aggregate.
- **Settle-before-delete reaper:** a non-empty `seat()` balance retires only via a
  `seat→player/ai` settlement transfer; the reaper settles before deleting, never zeroes.
- Gated by `CHIP_CUSTODY_ENABLED` (dev-on via `.env`, default off).

**Crucial shape confirmation:** custody landed as a **LEDGER PROJECTION**
(`CASH_MODE_CHIP_CUSTODY_SCOPE.md`), NOT a parallel parcel-state store — `AT_TABLE`
amount = the `seat(game_id)` balance; `IN_BANKROLL` = the entity's net position outside
any seat; `SETTLING` is the transient during the settle transfer. That's simpler than
the §5.2 parcel machine **and exactly the shape our escrow/split contract assumed.** The
tournament economy is the same pattern with **one net-new account: `tournament(id)`** +
`record_tournament_buy_in/payout` mirroring `record_ai_buy_in/cash_out`. The
settle-before-delete reaper is the template for escrow cleanup (distribute the escrow
before deleting the tournament row; never zero a non-empty escrow).

## The mapping (P2 concepts → state-model concepts)

| P2 concept | State-model concept | Consequence |
|---|---|---|
| `tournament_buy_in`, `tournament_payout` | **Transfers** (`record_transfer`, no `central_bank` side) between `player:<id>`/`ai:<id>` and a new `tournament:<id>` (or `entry:<id>`) escrow counterparty | Drift-invisible escrow earmarked by tournament; sibling of the shipped `seat:<game_id>` |
| `tournament_overlay` | **Bank pool DRAW** (creation toward the field) | Stays a `central_bank` creation reason — counts in drift math (it really moves reserves) |
| `rake` / wealth-tax | **Bank pool DEPOSIT** (`table_rake`/`rake_sink`) | Reuses the existing recyclable deposit reason; refills reserves |
| A tournament entry's chips | The `tournament:<id>` escrow **account balance** (custody is a ledger *projection*, not a parcel state machine — confirmed by what landed on dev) | `balance_of(tournament:<id>)` IS the at-escrow amount; sibling of `seat(game_id)`; settle-before-delete applies (never zero a non-empty escrow) |
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

## The escrow + payout-split contract (the runner never touches real chips)

Decided 2026-05-31. **The tournament runner is a pure function over funny money; the
circuit sandbox is the sole real-chip authority.** Three stages, three owners:

1. **Escrow-in (sandbox, at registration).** Each entrant's buy-in is moved INTO the
   `tournament:<id>` escrow:
   - human / AI buy-in → `player:<id>` / `ai:<id>` → `tournament:<id>` (a `record_transfer`)
   - bank overlay / bank-seeded AI / freeroll seed → bank-pool **DRAW** → `tournament:<id>`

   The escrow now holds the whole purse. **The overlay-vs-buy-in distinction is made
   HERE, by reason/category at the source** — overlay is a bank-pool draw (counts in
   drift), a buy-in is a drift-invisible transfer. (This closes codex's hole: the
   `tournament:<id>` counterparty alone does NOT distinguish them; the *reason* does.)

2. **Run (runner, funny money only).** The tournament plays its isolated
   `field_size × starting_stack` universe and emits a **payout split** — a list of
   `(recipient, percent_of_purse)` tuples summing to 1.0. The runner touches **zero**
   real chips and has no ledger knowledge; it only maps placements → percentages (the
   payout curve). Curve *shape* lives with the runner; purse *size* with the sandbox.

3. **Distribute (sandbox, at completion).** The sandbox drains the escrow per the
   tuples: for each, `tournament:<id> → recipient` for `round(pct × purse)`. After
   distribution the escrow is 0.

**The escrow-balance invariant (the whole real-chip contract):**

```
tournament:<id>  ==  Σ buy_ins + Σ overlays      (after escrow-in)
                 ==  Σ payouts + rake  →  0       (after distribute)
```

Edge rules so "the sandbox handles it" is unambiguous:

- **Rake** — simplest as a tuple in the split (`(rake_sink, pct)`) so the percents sum
  to 1.0 and the escrow fully drains; or pre-skim at escrow-in. Tuple-in-split preferred.
- **Rounding residual → top finisher** (no leakage; `pct × purse` won't divide evenly).
- **Idempotent distribution (I6)** — mark the escrow drained (a terminal flag) so a
  retry/restart can't double-pay; the generalised `ended_at`-style guard.

**Cross-scope audit (codex's 1a).** Career bankroll is *global* but the escrow + audit
are *sandbox-scoped*, so the player side of escrow-in/payout crosses scopes. Make it an
explicit **cross-scope transfer with BOTH sides audited** (or treat the career bankroll
as a ledger owner whose parcels attribute to the sandbox event) — otherwise the escrow
looks "funded from outside the sandbox" to the sandbox read-model. The escrow account is
the sandbox-scoped bridge; real chips cross only at escrow-in and distribute.

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
- **One signal, coordinated actuators (codex caveat).** The chairman is a *signal*,
  not two blind controllers. Both levers (tournament overlay/rake and cash-rake) must
  read **one consistent `EconomyState` snapshot per decision** and not each
  independently over-correct the same reserves — otherwise they fight and oscillate.
  Compute the snapshot once under the sandbox lock; both levers consume that value.

"Take from the bank vs take from the rich," resolved cleanly: **overlay** is a
`bank → tournament:<id>` draw (take from the bank); **rake / wealth-tax / tourist
buy-ins / staking** are transfers from the field into the escrow or `rake_sink`
(take from the rich). The chairman sets the rates off the one signal; the levers
are just which transfer reasons fire.

## Revised build order (replaces P2 §"Build order")

Phase 0 (the unified ledger substrate) is now **DONE on `development`** (custody
machine cut over, v129). On a tournaments branch rebased onto it:

0. **(state model, on `development`) — DONE.** Unified human+AI ledger, accounts
   (`player`/`ai`/`seat`/`bank`), `record_transfer`, `balance_of` (D2), AI parity,
   settle-before-delete reaper. Tournament work consumes it directly — no longer a gate.
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

- **Schema divergence.** `development` is now at **v129** (Presence + chip-custody
  cutover); the `tournaments` branch is at v124 (the tracker-drop migration). Merge
  `development` into `tournaments` first, then renumber the tracker-drop migration (and
  any P2 migrations) above v129 — same collision class as the circulating-flag v123
  incident.
- **Same file, two efforts** (`core/economy/ledger.py`) — **resolved by sequencing.**
  The state model's ledger substrate is now ON `development`, so the move is: merge dev
  into tournaments, then add `tournament(id)` + `record_tournament_buy_in/payout` on top
  of the landed `seat()`/`record_ai_buy_in/cash_out` pattern — not a parallel build.
- **Sign-off carried over:** the state model's pivotal table-as-projection decision
  (its §6) is accepted (D1) but unbuilt; the tournament economy does not depend on
  it (tournaments have no `cash_tables` seat map), so P2 can proceed on Phase 0 +
  the chairman without waiting for cash Phase 3.
