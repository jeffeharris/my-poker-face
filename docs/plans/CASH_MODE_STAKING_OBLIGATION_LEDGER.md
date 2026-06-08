---
purpose: Unify all cash-mode staking (aspiration, take_stake, human sponsor, backing/loans) under one double-entry obligation ledger whose per-contract conservation is structurally checkable, and consolidate the vocabulary on "staking"
type: design
created: 2026-06-08
last_updated: 2026-06-08
---

> **Review status (2026-06-08):** reviewed by the `code-architect` agent and
> `/codex-assist`. Verdict: **sound with changes.** The corrections below are
> folded in. The headline fix: the obligation tracks **principal only** — the
> staker's profit share is a separate chip flow with *no* obligation counterpart
> (codex caught that the original `oblig -= staker_payout` made the invariant
> false for every winning stake). See "## Review corrections" at the end for the
> full list and who flagged each.

# Cash Mode — Staking Obligation Ledger

## TL;DR

A stake's chips commingle with gameplay winnings on the borrower's seat, so the
chip ledger **cannot** express a per-contract conservation invariant — which is
exactly why the wrong-seat mint (prod drift 2026-06-08) was invisible until it
summed to −2.3M. This doc introduces a second accounting dimension — an
**obligation ledger** — that tracks the *claim* ("borrower owes staker") apart
from chip custody. Every stake then owns its own conservation: the obligation
created at funding must equal what is extinguished + carried + forgiven at
terminal, and this holds regardless of how chips sloshed through the seat.

It also **unifies** the three things that are the same shape today — AI
aspiration/take_stake, human sponsorship, and the backing/loan system — under
one model, and **consolidates the vocabulary on "staking"** (there is no
"backing" or "loan"; there is a stake, a staker, and a borrower).

This supersedes the funding/settlement-guard slice already shipped in PR #235
(`CASH_MODE_STAKE_STATE_MACHINE.md`), which closed the leak but left the
per-contract invariant unexpressible. That guard becomes one half of the
two-sided reconcile defined here.

---

## Why chips alone can't do this (the crux)

`CASH_MODE_STAKE_STATE_MACHINE.md` wanted invariant *"Σ ledger rows for a stake
== 0"* and backed off to *"aggregate seat balance ≥ 0"* because the per-contract
version is false at the chip layer:

> Funding puts `principal` on the borrower's seat. The borrower then wins/loses
> against other players — un-ledgered seat-to-seat sloshing. Settlement splits
> `principal + cut×profit` back out. "The stake's chips" cannot be isolated from
> "the borrower's winnings" on a shared seat, so the stake's chip rows net to
> `±profit`, not zero.

No chip account *owns* the stake's conservation, which is why a misrouted
funding row (principal credited to `seat:<staker>` instead of `seat:<borrower>`)
minted silently. The fix in PR #235 routes funding to the right seat and adds a
settle-time guard, but the underlying gap — *a stake is not a self-conserving
entity* — remains. The obligation ledger closes it.

---

## What exists today (don't rebuild)

- `cash_mode/stakes.py` — `Stake` frozen dataclass; statuses
  `{active, settled, carry, defaulted}`; terms + realized outcome in the
  `stakes` table (schema v98/v106): `principal`, `cut`, `format`
  (`pure`/`match_share`/`house`), `match_amount`, `origination_fee`,
  `staker_id`/`kind`, `borrower_id`/`kind`, `staker_payout`, `borrower_payout`,
  `status`, `carry_amount`, `resolution`, `settled_at`.
- `cash_mode/stake_chip_flow.py` — `build_stake_creation_flows` /
  `build_stake_settlement_flows`; `DIRECTION_*` constants (correct chip
  directions).
- `cash_mode/stake_settlement.py` — the split math.
- `cash_mode/stake_lifecycle.py` — `fund_climb_stake` (single funding site, both
  AI paths now routed through it) + `assert_stake_funding_reached_borrower_seat`
  (settle-time guard, invariant 3) + `unwind_climb_funding`. **PR #235.**
- `poker/repositories/chip_ledger_repository.py` — `balance_of(account)`,
  `entries_for_stake(stake_id)`, the `record_*` flow helpers in
  `core/economy/ledger.py`. **`record_ledger_reconciliation` already uses a
  bank-neutral `reconciliation` suspense account invisible to drift** — the
  precedent this design extends.
- **The schema is already ~85% on "staking":** the `stakes` table replaced the
  `active_loan_*` columns (v98), which were dropped (v99); `house_loan_*` ledger
  reasons were renamed `house_stake_*` (v90). There is no live `loan`/`backing`
  column or ledger reason to migrate.

---

## The obligation ledger

### Accounts

A new account namespace, never a chip account, so it cannot touch the chip
conservation sums:

| Account | Meaning |
|---|---|
| `oblig:<stake_id>` | Outstanding amount the borrower owes the staker on this stake. Balance > 0 ⇒ live debt; 0 ⇒ extinguished. |
| `oblig_genesis` | Contra for origination (the claim coming into existence). |
| `oblig_settled` | Contra for repayment (claim extinguished by a real payoff). |
| `oblig_forgiven` | Contra for write-off (default / house forgiveness — bad debt). |

All four are in `chip_ledger_entries` (reuse `balance_of` / `entries_for_stake`),
and every obligation row is `oblig_* → oblig:<id>` or `oblig:<id> → oblig_*`:
**both ends are in the `oblig*` namespace — no obligation row ever sources or
sinks a chip account, `central_bank`, or a seat.**

**Drift isolation is structural, not reason-based** (both reviewers corrected the
original framing). `compute_audit` builds `ledger_outstanding` from
`sum_creations_by_reason`/`sum_destructions_by_reason`, which first filter
`source = 'central_bank'` / `sink = 'central_bank'` and only then group by reason
(`chip_ledger_repository.py:189`). A bank-neutral obligation row matches neither
filter, so it is invisible to the drift math **regardless of its reason**.
`actual_outstanding` only sums chip surfaces (bankrolls/seats/loans), which
obligation rows never credit. So the isolation is automatic — *provided the rows
are written via `record_transfer` (bank-neutral) and never `record` (which
requires a central_bank side)*. The P1 keystone test ("drift bit-identical
with/without obligation rows") therefore really proves *no obligation write
accidentally used `record`*. A new reason set
`OBLIGATION_REASONS = {stake_originate, stake_extinguish, stake_forgive}` is still
needed, but only to keep these reasons **out of `BANK_POOL_DEPOSIT_REASONS` /
`BANK_POOL_DRAW_REASONS`** (the one sum path that iterates named reasons) — not as
the primary isolation mechanism.

### The obligation tracks PRINCIPAL ONLY (the key correction)

The obligation is the **debt**: what the borrower must return to the staker —
the `principal`. It is **not** the staker's payout. The staker's *profit share*
(`cut × winnings`) is earnings distribution, not debt repayment, so it has **no
obligation counterpart** — it's a pure chip flow off the borrower's seat,
bounded by the borrower's actual winnings (`S − principal`). Folding profit into
the obligation makes the invariant false for every winning stake (extinguishing
`staker_payout > principal` drives `oblig:<id>` negative). Likewise the
borrower's own `match_amount` (match_share) and the `origination_fee` (paid at
origination, already settled) are **not** in the obligation — only `principal`.

### Lifecycle — chip dimension (unchanged) + obligation dimension (new)

```
●─▶ ORIGINATED ─▶ FUNDED ─▶ ACTIVE ─▶ SETTLING ─┬─▶ SETTLED   (oblig → 0, principal recovered)
    terms agreed  principal  borrower  snapshot   ├─▶ CARRY     (oblig = unrecovered principal)
                  on borrower playing   stack S    └─▶ FORGIVEN  (oblig → 0 via write-off)
                  seat                              (DEFAULTED = forgiven w/ relationship hit)
```

Let `recovered = min(S, principal)` (principal the staker gets back) and
`profit_share = max(0, staker_payout − principal)` (earnings, chip-only).

| Transition | Obligation entry (NEW) | Chip entry (existing) | Reconcile |
|---|---|---|---|
| **ORIGINATE→FUNDED** | `oblig_genesis → oblig:<id>` = **`principal`** (never match_amount / fee) | `staker_bankroll → seat(borrower)` (`stake_fund`); house: `central_bank → seat(borrower)` | **`Δ oblig:<id>` (=principal) == principal funded to `seat(borrower)`.** Seat is `ai_seat(sb,borrower)` for AI / `seat(game_id)` for human borrowers; **house stakes exempt from the wrong-seat leg** (`central_bank` has no staker seat to confuse) — assert `issue == principal` instead. |
| **FUNDED→ACTIVE** | none (marker) | none | seat carries `principal` (+ match). |
| **ACTIVE→SETTLING** | none — snapshot final seat stack `S` | none | one-shot CAS on status (kills double-settle). |
| **SETTLING→SETTLED** (`S ≥ principal`) | `oblig:<id> → oblig_settled` = **`principal`** | `seat(borrower) → staker` = `staker_payout`; `seat(borrower) → borrower` = remainder | **principal recovered == `principal`** (obligation zeroes); **`profit_share` ≤ `S − principal`** (chip layer); `staker_payout ≤ S`. |
| **SETTLING→CARRY** (`S < principal`) | `oblig:<id> → oblig_settled` = **`recovered` (=S)** | `seat(borrower) → staker` = `S` (all that's there) | after, `balance_of(oblig:<id>) == principal − recovered == carry_amount`. |
| **SETTLING→FORGIVEN/DEFAULTED** | `oblig:<id> → oblig_forgiven` = **residual** | `forgive_balance` annotation (no chips move) | after, `balance_of(oblig:<id>) == 0`; write-off lands in `oblig_forgiven`. **Every** default/forgive path must emit this row (not only the house-bust annotation). |

### The per-contract invariant (principal conservation — now actually true)

```python
# At any terminal state, per stake, in the obligation dimension ONLY:
principal_originated == recovered(→oblig_settled) + carried(balance_of(oblig:<id>))
                                                   + forgiven(→oblig_forgiven)
```

This holds for winners and losers alike because **profit never enters the
obligation**. It survives chip commingling (stated entirely in the obligation
dimension), and the reconcile ties it to chips at exactly the two points the
mint vector lives: principal reaches the borrower seat at funding; principal is
recovered from that seat at settle. Profit-share conservation stays the chip
layer's job (`profit_share ≤ S − principal`, can't exceed what was actually won).

### Two-sided reconcile, precisely (composes with the PR #235 guard)

- **Origination:** `Δ oblig:<id> == principal funded to seat(borrower)`, with the
  borrower-seat resolved per borrower kind (AI vs human) and **house stakes
  exempt from the wrong-seat leg**. This is the funding-time half; the PR #235
  guard (`assert_stake_funding_reached_borrower_seat`) is the settle-time half —
  they don't overlap, they bracket the contract.
- **Settle:** `recovered == principal portion of chips paid to staker
  (= min(staker_payout, principal))`; the profit portion is checked against the
  chip layer (`≤ S − principal`), not the obligation.

### Carry / default / forgiveness become balances

- **Carry** = `balance_of(oblig:<id>) > 0` after settle = unrecovered principal,
  drawn down by later voluntary payoffs (`oblig:<id> → oblig_settled`).
  **`balance_of(oblig:<id>) == 0` while `status == 'carry'` is the
  "carry-fully-repaid" state** — either add a `repaid` status or treat
  status+balance together (don't read status alone).
- **Default / forgiveness** = write-off to `oblig_forgiven`. Keep the
  status distinction (`forgiven`/`settled` vs `defaulted`) for analytics; both
  emit the same `oblig:<id> → oblig_forgiven` extinction so the invariant reads
  consistently.

### Carry / default / forgiveness become balances, not status strings

- **Carry** = `balance_of(oblig:<id>) > 0` after settle. The residual debt is a
  live account balance, queryable, that the next settlement or a voluntary
  payoff draws down — replacing the `carry_amount` column's bespoke arithmetic.
- **Default / forgiveness** = a write-off to `oblig_forgiven` (bad debt). The
  aggregate `balance_of(oblig_forgiven)` is the system's total forgiven debt — a
  real economic figure for the admin economy view.

---

## Unifying backing / loans

The backing/loan system tracks the same shape: a human sponsors an AI (or vice
versa), principal is advanced, repayment owed. Today its remnant is the
`active_loans_principal` audit term (chips on a staked human's seat). Under this
design:

- Backing **is** staking with `borrower_kind=human` / `staker_kind=human`.
- `active_loans_principal` is **re-derived** from `Σ balance_of(oblig:<id>)` over
  active human-borrower stakes. But this is **not** a drop-in — the current term
  is `SUM(principal + match_amount) WHERE borrower_kind='human' AND
  status='active'` (`_sum_active_stake_principal_for_humans`). The re-derivation
  must preserve that exactly, which constrains the obligation model:
  - **Filter to `borrower_kind='human'` AND `status='active'`** — AI-borrower
    obligations are already captured in AI seat sums (double-count if included);
    **CARRY stakes are excluded** (their chips already left the seat at settle —
    a carry balance is an off-seat receivable, not chips in the universe).
  - **`match_amount` is NOT in the obligation** (principal-only model), yet the
    legacy term counts `principal + match_amount`. So for `match_share` the
    `match_amount` portion must still come from the `stakes` column (or a
    parallel seat-funding row), not the obligation balance. The
    `_sum_live_session_human_stacks` subtraction (`borrowed = principal +
    match`) must be reconciled with this split or it double-counts.
  - **Legacy stakes (no obligation rows at deploy)** must fall through to the SQL
    aggregate, not read `balance_of == 0`.
  - Ship the re-derivation behind its **own flag**, defaulting to the existing
    SQL sum, so the two can be compared in prod before cutover.
- No new feature surface — backing routes through the same originate/settle
  chokepoints. This is the payoff that makes the obligation ledger a
  *simplification* once the above is handled: three conservation schemes collapse
  to one.

---

## Vocabulary consolidation — "staking"

One word. A **stake** between a **staker** and a **borrower**; the act is
**staking**. No "backing", "backer", "loan", "lender", "sponsor-loan".

- **New code** (this work): pure `stake*` — accounts, reasons, types, comments.
- **Rename** (same branch): residual user-facing React strings (`loan` ~81,
  `backing` ~26, `backer` 2) → staking vocabulary; live `backing`/`loan` code
  identifiers + comments.
- **Leave intact** (deliberate): migration history in `schema_manager.py`
  (`_migrate_v89_add_loan_fields`, the v89–v99 docstrings) — an immutable audit
  trail of what each migration did *at the time*; renaming it would misstate
  history and risks the applied-migration tracking. Doc *titles* like
  `CASH_MODE_BACKING_SYSTEM_HANDOFF.md` get a one-line "now: staking" banner, not
  a file rename (preserve inbound links).
- One open product choice: "sponsor" is arguably a distinct *relationship* (a
  human backing an AI) vs the *mechanism* (staking). See Open Q3.

---

## Rollout — P1 shadow + P2 flip (one branch, per decision)

Both phases land together (the user opted for shadow + invariant-flip), but as
**reviewable commits in sequence** so the flip is isolatable in `git`.

**P1 — shadow-write (no behavior change).**
1. Add the `oblig*` account builders + `record_obligation()` helper +
   `OBLIGATION_REASONS` in `core/economy/ledger.py`. The new reasons must be
   added to `LEDGER_REASONS` **and** `TRANSFER_REASONS` (else `record_transfer`
   rejects the write) and kept out of `BANK_POOL_*`. `record_obligation` wraps
   `record_transfer` (bank-neutral) — **never `record`**, which requires a
   central_bank side and would reject the row.
2. Write obligation rows at originate (`fund_climb_stake`, `record_house_stake_issue`,
   the human sponsor accept in `cash_routes.py`) and at every settle/default/
   forgive path (`_settle_one_departing_ai_stake`, the human-leave path,
   `ai_carry_resolution`, the staker-forgive route). **Atomicity is mandatory:**
   the obligation row, the `stakes` status flip, and the chip rows must commit in
   the **same unit of work** (`chip_unit_of_work`) — a shadow phase that writes
   obligations outside the chip transaction can create inconsistent obligations
   (rows that don't match chips) on any partial failure. Every obligation row
   carries `context.stake_id` (for `entries_for_stake`) and `sandbox_id`.
3. **Drift-isolation test (the keystone of P1):** a stake's full lifecycle
   writes obligation rows, and `compute_audit().drift` is **bit-identical** with
   and without them. Per the reviewers this really asserts *no obligation write
   used `record` instead of `record_transfer`* — the only way a bank-neutral row
   could leak into the sums.

**P2 — flip the invariant.**
4. `assert_stake_funding_reached_borrower_seat` gains the originate-side leg
   (`Δ oblig:<id> == principal funded to seat(borrower)`, borrower-seat resolved
   per kind, **house exempt from the wrong-seat leg**) and the settle-side leg
   (`recovered == min(staker_payout, principal)`). Same enforce/alarm flag
   (`STAKE_SETTLE_GUARD_ENFORCE`).
5. Carry/default/forgiveness read `balance_of(oblig:<id>)` (status read together
   with balance — `balance==0 & status=='carry'` ⇒ repaid). `carry_amount`
   retained as a denormalized cache, asserted equal during a deprecation window.
6. `active_loans_principal` re-derived from obligations **behind its own flag**
   (defaults to the existing SQL sum), with the human-only / active-only /
   carry-excluded / match_amount / legacy-fallthrough handling above, so it can
   be compared against the legacy term in prod before cutover.

**Legacy:** stakes active at deploy have no obligation rows; their settle takes
the alarm-only path (P2 guard logs, doesn't block) until they drain. No
backfill — the obligation ledger is forward-only, like the chip ledger's
`pre_ledger_universe` seed was.

---

## Migration & schema

- **No new table.** Obligation accounts live in `chip_ledger_entries`. The only
  schema touch is the new reason strings (no DDL — reasons are TEXT).
- Optional later: a `funded_seat` column on `stakes` to make the bound seat
  explicit/queryable for the audit (deferred; the ledger rows already carry it).

---

## Test plan

- **Unit:** each transition emits exactly the expected obligation + chip rows
  from stored terms; `oblig:<id>` zeroes on SETTLED/FORGIVEN, equals
  `carry_amount` on CARRY; originate always credits `seat(borrower)`.
- **Conservation (the keystone):** `compute_audit().drift` identical with/without
  obligation rows (P1); aggregate `Σ balance_of(oblig:<id>)` over active stakes
  == `active_loans_principal` (P2).
- **Adversarial:** a deliberately misrouted funding (credit `seat:<staker>`)
  fails the two-sided guard at originate *and* settle; a double-settle is a
  no-op (one-shot CAS).
- **Sim:** run the cash economy with aspiration + take_stake + human sponsor
  enabled; assert aggregate seat balance stays ≥ 0 and every closed stake's
  obligation account terminal-zeroes (or equals its carry).
- **Vocabulary:** a lint/grep guard fails CI if new `loan`/`backing`/`backer`
  identifiers appear outside migration history.

---

## Resolved questions (both reviewers concur)

1. **match_share borrower contribution → obligation tracks `principal` only.**
   The borrower's `match_amount` is self-funded (a stake in themselves), not a
   debt to the staker; including it leaves a phantom residual at settle (the
   staker recovers `principal` but the obligation was originated for
   `principal + match`). `origination_fee` likewise is settled at origination,
   not a forward debt. The `match_amount` chip-surface accounting stays in the
   `stakes` column / chip layer (see Unifying backing/loans).
2. **Snapshot `S` at SETTLING is sufficient.** Per-hand attribution would mean a
   margin account (tag every pot award with a stake_id) — the design explicitly
   accepts chip commingling as unresolvable and audits at the endpoints instead.
   The `S` snapshot happens at the CAS that flips to SETTLING, so it's
   race-protected.
3. **Keep "sponsor" as UI/product copy only; "staking" everywhere in code.** The
   human→AI social framing can stay "sponsor" in player-facing strings; all
   mechanism code (identifiers, reasons, accounts, logs, tests) is "staking".
   There are no `sponsor` *code* identifiers needing later unification.
4. **Same `chip_ledger_entries` table + `oblig*` namespace.** No separate table.
   Discipline: the per-contract invariant is always read via the per-stake
   `oblig:<stake_id>` account, **never** the `oblig_genesis/settled/forgiven`
   contras (those are meaningful only in aggregate) — document this in the ledger
   module so an `oblig_genesis` balance is never mistaken for a chip balance.

## Review corrections (2026-06-08) — folded in above

| # | Severity | Flagged by | Correction |
|---|---|---|---|
| 1 | **Critical** | codex | Obligation tracks **principal only**; profit-share is a chip-only flow. Original `oblig -= staker_payout` made the invariant false for winning stakes. |
| 2 | High | both | Drift isolation is **structural** (bank-neutral via `record_transfer`), not reason-exclusion. The P1 test really proves no row used `record`. |
| 3 | High | architect | **House stakes fund `seat(game_id)`** not `ai_seat(sb,borrower)` → exempt from the wrong-seat reconcile leg (mint is structurally impossible for house). |
| 4 | High | both | `active_loans_principal` re-derivation: human-only, active-only, **exclude CARRY**, `match_amount` stays chip-layer, legacy fallthrough, **own flag**. |
| 5 | High | codex | **Atomicity**: obligation + status + chip rows in one `chip_unit_of_work`, or shadow phase creates inconsistent obligations. |
| 6 | Med | both | Write-path traps: new reasons in `LEDGER_REASONS` + `TRANSFER_REASONS`; rows carry `stake_id` + `sandbox_id`; use `record_transfer` not `record`. |
| 7 | Med | codex | **Every** default/forgive path emits a `stake_forgive` extinction row (not only house-bust); keep `forgiven`/`defaulted` status distinction for analytics. |
| 8 | Med | both | "Carry fully repaid" = `balance==0 & status=='carry'` — add a `repaid` status or read status+balance together; never status alone. |

---

## Related

- `CASH_MODE_STAKE_STATE_MACHINE.md` — the funding/settle-guard slice (PR #235)
  this supersedes for the conservation model.
- `CASH_MODE_BACKING_SYSTEM_HANDOFF.md` — Phase 1 staking entity / flow emitters
  (now: staking).
- `CASH_MODE_CHIP_LEDGER_HANDOFF.md` — the chip audit (`drift = ledger − actual`)
  the obligation dimension must stay invisible to.
- `CASH_MODE_AI_ASPIRATION_ASK.md` — the aspiration feature spec.
