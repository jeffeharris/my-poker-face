---
purpose: Make a stake a guarded lifecycle entity whose chip flows are derived from its stored terms and reconciled per-contract, closing the aspiration funding leak
type: design
created: 2026-06-08
last_updated: 2026-06-08
---

# Cash Mode — Stake Contract State Machine

## Implementation status (2026-06-08)

**Shipped (branch `fix/stake-state-machine-aspire-funding`, not deployed):**
- `cash_mode/stake_lifecycle.py` — the single funding site `fund_climb_stake`
  (debits the staker, credits the **climber's** seat) + `unwind_climb_funding`
  (reverses both int and ledger on a create-stake failure).
- `cash_mode/lobby.py::_process_aspiration_asks` routed through it; the
  `debit_bankroll_for_seat(staker_id, …)` call (which credited the staker's own
  seat) is gone.
- `tests/test_stake_lifecycle.py` + updated `test_aspiration_atomicity.py`.
  Full `test_cash_mode/` aspiration + conservation suites green; the funding
  fix is unit-proven (principal lands on `seat:<climber>`, not `seat:<staker>`).

**Refined diagnosis (three coupled bugs, all in the aspiration path):**
1. **Funding targeted the wrong seat** — `debit_bankroll_for_seat(staker_id)`
   credited `seat:<staker>`; settlement drains `seat:<borrower>`. *(FIXED)*
2. **The climber's `from_seat` was padded with `+ principal`**, overdrawing the
   seat and feeding an inflated `chips_at_leave` to settle. *(Subsumed: with
   funding now landing on the climber's seat, the `+principal` is exactly the
   chips the seat holds — conserved.)*
3. **The stake settled in the SAME tick it was created** — `_settle_table_stakes`
   read the climb-vacate's `from_seat` as a session end, so the staked session
   never happened and the climber's existing chips got split with the staker as
   fake "winnings." *Behavioral*, not a mint once funding is correct. *(FIXED:
   `_settle_table_stakes` now skips pids whose decision this tick is
   `aspiration_climb` — a tier MOVE, not a session end. The stake stays ACTIVE
   and settles on the real leave from the higher tier. The one-active-stake gate
   guarantees the only active stake for that pid is the one just created, so
   nothing else is owed a settle. Covered by
   `test_aspiration_climb_stake_not_settled_same_tick` + control.)*

**Key correction to the model below:** an aspiration stake is a **grubstake**
(staker bankroll → climber, climber re-buys at the new tier), not a seat-funding
of an already-seated borrower like the human-sit path. The funding therefore
routes to the climber, and the per-contract `Σ rows == 0` "assertion" sketched
below is trivially true in double-entry — the real invariant is **aggregate
seat balance ≥ 0**, checked by the sim's `audit_drift` and the
`test_refresh_ticks_preserve_drift` conservation test.

---

# Cash Mode — Stake Contract State Machine

## TL;DR

A stake today has a `status` *label* but no enforced lifecycle and no
per-contract conservation check. The AI aspiration ("get staked to climb a
tier") path funds the **wrong seat**, and nothing notices, so each aspire deal
mints chips. This doc proposes making a stake a **guarded state machine** whose
transitions are the *only* places chips move, where every chip flow is derived
from the stored terms and the contract is asserted to net to zero before it can
close.

This is the "stop the bleeding" design. Cleaning the existing drift is a
separate pass (see [Rollout](#rollout)).

---

## The problem this closes

### Symptom
Admin → Chip Economy, sandbox `bfa7050b` reports **drift −2,157,105**. The
real, provable component is an **aggregate seat balance of −1,331,024**, which
is mathematically impossible in a closed table (seat balances can only sum to
≥ the chips currently sitting on seats). ~1.3M chips left seats that never
entered via a ledger row.

### Root cause — the "aspire" funding bug
**Aspiration** is the AI-to-AI "get staked to play a bigger table" feature
(`cash_mode/lobby.py::_process_aspiration_asks`,
spec `CASH_MODE_AI_ASPIRATION_ASK.md`). A low-tier AI rolls an
`aspiration_bias`-driven ask; a wealthier AI (the *staker*) puts up the
`principal`; the climber plays the higher tier; at settle they split per the
agreed `cut`.

Intended chip flow (per the function's own docstring, step 5):
```
staker bankroll  ──principal──▶  climber bankroll  ──▶ climber's new seat
                                       ... play ...
climber seat     ──principal + cut×winnings──▶  staker
```

Actual chip flow (`lobby.py:~4994`):
```python
debited = debit_bankroll_for_seat(bankroll_repo, staker_id, principal, ...)
```
`debit_bankroll_for_seat` debits the staker **and writes an `ai_buy_in`
crediting `seat:<staker>`** — the *staker's own seat*. The climber's seat is
never funded. At settle, the staker is paid out of `seat:<climber>`:

```
actual:  staker bankroll ──principal──▶ seat:STAKER        (orphaned; re-cashed later)
         seat:CLIMBER    ──principal + cut──▶ staker        (paying chips it never received)
```

The climber's seat pays out the principal it was never given → goes negative →
those chips are minted. The principal stranded on the staker's seat is later
recovered *again* when the staker cashes out their own seat. Net: the staker
recovers principal twice; the climber's seat absorbs the deficit. Summed over
all aspire deals, that deficit is the −1.3M phantom.

### Evidence (prod, read-only, 2026-06-08)
- AI `stake_fund` ledger rows in `bfa7050b`: **0** (the 23 that exist are all
  `player_stake_principal` — a *human* funding an AI). The AI funding step
  never writes a borrower-seat funding row.
- Seat-sourced `stake_payoff` (`site=ai_stake_settle_staker`): **1,180,896** —
  same order of magnitude as the −1.33M leak.
- Example `ai_stake_aspire_072014ade254`: staker `marie_antoinette`,
  borrower `don_quixote`, `principal=8000`, `cut=0.3`, `staker_payout=8897`,
  `borrower_payout=2093`, and **`created_at == settled_at`** (18:20:45, seconds
  after sandbox boot) — i.e. created and settled in the same instant.
- The standard bankroll-vs-ledger reconcile is **clean** (1 row off by 7,206),
  because the phantom chips carry *valid-looking* `ai_cash_out`/`stake_payoff`
  rows. The leak is only visible as the impossible negative seat aggregate.

### Why the usual guards miss it
- Bankroll-vs-ledger reconcile passes (rows look valid).
- Bank-pool depth passes (seats aren't pool deposit/draw reasons).
- `stake_id` is stamped on the *settle* rows but **not the funding rows**, so
  nothing can sum a single stake and check it nets to zero.

The deeper issue: **a stake is not an entity that owns its own conservation.**

---

## What already exists (don't rebuild)

- `cash_mode/stakes.py` — `Stake` frozen dataclass, statuses
  `{active, settled, carry, defaulted}`.
- `stakes` table (schema v98) stores the full **terms** per `stake_id`:
  `principal`, `cut`, `format` (`pure`/`match_share`/`house`), `match_amount`,
  `origination_fee`, `stake_tier`, `staker_id`/`staker_kind`,
  `borrower_id`/`borrower_kind` — **and the realized outcome** (v106):
  `staker_payout`, `borrower_payout`, `status`, `carry_amount`, `resolution`,
  `settled_at`.
- `cash_mode/stake_chip_flow.py` — `build_stake_creation_flows` /
  `build_stake_settlement_flows` already emit the *correct* directions
  (`DIRECTION_STAKER_TO_BORROWER_SEAT`, etc.).
- `cash_mode/stake_settlement.py` — the split math.

**The terms are stored and the correct flow emitters exist.** The gap is purely
that (a) the aspire path bypasses the emitters and funds the wrong seat, and
(b) nothing reconciles the realized chips against the stored terms.

---

## The state machine

```
                 ┌──── refuse: staker can't cover principal ────┐
                 │                                               ▼
  ●──▶ PROPOSED ─┴─▶ FUNDED ──▶ ACTIVE ──▶ SETTLING ──┬──▶ SETTLED      CANCELLED
       (terms        (principal  (borrower (leave:     │   (clean,        (terminal,
        agreed,       on the     playing)  snapshot    │    in == out)     no chips
        no chips)     BORROWER             final       │                   moved)
                      seat)                stack S)    ├──▶ CARRY
                                                       │   (S < principal; residual debt)
                                                       └──▶ DEFAULTED / FORGIVEN
                                                           (house forgive / insolvency valve)
```

Terminal states: `SETTLED`, `CARRY`, `DEFAULTED`, `FORGIVEN`, `CANCELLED`.

### Transitions

Every transition is the **only** place chips may move for that stake, and every
ledger row it writes carries `context.stake_id`.

| Transition | Trigger | Chip movement (ledger reason) | Guard / invariant |
|---|---|---|---|
| `→ PROPOSED` | staker accepts ask | none | staker projected-solvent for `principal` |
| `PROPOSED → FUNDED` | commit | **`staker bankroll → seat(borrower_id)`** (`stake_fund`, **+stake_id**); `match_share`: borrower bankroll → own seat; `pure`: origination fee borrower→staker; `house`: `central_bank → seat(borrower_id)` (`house_stake_issue`) | atomic; refuse → `CANCELLED`. **Destination seat MUST equal `seat(stake.borrower_id)`.** |
| `PROPOSED → CANCELLED` | funding refused / create fails | none, or exact reversal of any pre-placed row | terminal |
| `FUNDED → ACTIVE` | borrower sits at target tier | none (marker) | seat carries `principal` (+ match) |
| `ACTIVE → SETTLING` | leave-table | none — snapshot final seat stack `S` | **one-shot CAS on status** (a second leave is a no-op → kills double-settle) |
| `SETTLING → SETTLED` | split computed | `seat(borrower) → staker bankroll` (`stake_payoff`); `seat(borrower) → borrower bankroll` (`ai_cash_out`); both **+stake_id** | **conservation assert (below)** before status flips |
| `SETTLING → CARRY` | `S < principal` | partial recovery to staker; residual → `carry_amount` | residual ≤ principal |
| `SETTLING → DEFAULTED / FORGIVEN` | house forgive / insolvency valve | `forgive_balance` annotation | terminal |

### The conservation invariant (the keystone)

`SETTLING → SETTLED` must not flip the status unless **all** hold, using the
**stored terms** to derive expectations:

```python
expected = settle_math(principal, cut, format, match_amount, final_stack=S)

# 1. The contract nets to zero across all its ledger rows.
assert sum(amount_signed for row in ledger.rows_where(stake_id=sid)) == 0

# 2. Realized chip flows match the terms-derived split.
assert ledger_paid_to(staker_id)   == expected.staker_payout
assert ledger_paid_to(borrower_id) == expected.borrower_payout

# 3. The funding actually reached the borrower's seat.
assert funding_row.sink == seat(stake.borrower_id)     # <-- catches the aspire bug
```

Invariant (3) is the one that fails today: the stored term says
`borrower=don_quixote`, but the funding row's sink is `seat:marie_antoinette`.
A violation **blocks the settle and raises/alarms** instead of silently minting
into the staker's bankroll.

---

## Mapping onto the code (incremental, not a rewrite)

1. **States** — extend the status literal set in `cash_mode/stakes.py` with
   `proposed`, `funded`, `settling`, `cancelled`, `forgiven`. The dataclass and
   table already exist; new states read NULL-safe on legacy rows.
2. **One transition module** — `cash_mode/stake_lifecycle.py` exposing
   `transition(stake, event, ...) -> (Stake, [StakeChipFlow])`. It emits flows
   **only** via the existing `build_stake_creation_flows` /
   `build_stake_settlement_flows`. No caller writes stake chip flows directly.
3. **Route both paths through it.**
   - Aspiration: `lobby.py::_process_aspiration_asks` — **delete the
     `debit_bankroll_for_seat(staker_id, ...)` call** and drive the
     `PROPOSED → FUNDED` transition, which funds `seat(borrower_id)`.
   - Human sponsor/sit: `flask_app/routes/cash_routes.py` sit/leave paths —
     same transition entry points.
4. **Conservation assert** — implement against
   `chip_ledger_repo`, summing rows by `context.stake_id`, in the
   `SETTLING → SETTLED` step. Reuse the `balance_of` summing style.
5. **Stamp `stake_id` on funding rows** — no migration (it's JSON context).
   Optional: add a `funded_seat` column to make the bound seat explicit and
   queryable for the audit.

---

## Open design fork — is "aspire" even a stake?

The traced aspire deals have **`created_at == settled_at`** — created and
settled in the same instant, with computed (not played-out) winnings. If
aspiration is really an *instantaneous tier-climb transfer* rather than a
borrow-play-settle contract, then forcing it through the full
`FUNDED → ACTIVE → SETTLED` lifecycle is the wrong shape — it never genuinely
goes `ACTIVE`.

Two options:
- **A. Keep it a stake** but ensure the lifecycle runs honestly (funds the
  borrower seat, borrower actually plays the tier, settles at real leave-time).
- **B. Make aspire a distinct atomic op** — a single conservation-checked
  `staker bankroll → climber bankroll` transfer (the climber then buys in
  normally), with no seat funding and no settlement contract at all.

Recommendation: **B** for the climb itself (it's a grubstake, not a session
stake), reserving the full state machine for genuine session-length staking.
This needs sign-off from whoever owns the aspiration feature.

---

## Rollout

Two separate PRs — do not mix them.

1. **Stop the bleeding** (this doc). The state machine + the aspire funding fix.
   Closes the leak for all *new* stakes. Add the settle-time conservation
   assert as a hard failure in dev/sim, and as an alarm (not a crash) in prod
   initially so we can observe before enforcing.
2. **Clean the existing drift.** The ~1.3M phantom is already in AI bankrolls
   with valid-looking rows; the state machine won't claw it back. Run
   `python -m cash_mode.ledger_reconcile --apply` with the backend quiesced
   (WAL checkpoint first — see `reference_sqlite_wal_backup`), after deciding
   the policy: absorb the phantom into `central_bank`, or haircut the inflated
   bankrolls. Back up the DB first (sqlite backup API, not `cp`).

### Test plan
- Unit: each transition emits exactly the expected flows from given terms;
  `FUNDED` always targets `seat(borrower_id)`; double-`SETTLING` is a no-op.
- Property/sim: run the cash economy sim with aspiration enabled and assert the
  **aggregate seat balance stays ≥ 0** and per-stake `Σ rows == 0` for every
  closed stake. This is the check that would have caught the bug originally.
- Regression: a stake whose funding is deliberately misrouted must fail the
  settle assert (don't let the fix silently no-op).

---

## Related

- `CASH_MODE_AI_ASPIRATION_ASK.md` — the aspiration feature spec.
- `CASH_MODE_BACKING_SYSTEM_HANDOFF.md` — Phase 1 staking (the `Stake` entity,
  flow emitters).
- `CASH_MODE_CHIP_LEDGER_HANDOFF.md` — the audit (`drift = ledger − actual`).
- `CASH_MODE_SESSION_LIFECYCLE_HARDENING.md` — the double-settle bug class this
  state machine's one-shot `SETTLING` transition also guards against.
- `CASH_MODE_AI_STAKER_INCENTIVES.md` — staker-side behavior/economics.
