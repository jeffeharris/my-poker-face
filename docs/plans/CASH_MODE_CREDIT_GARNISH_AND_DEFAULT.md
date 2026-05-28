---
purpose: Fix the runaway-debt failure mode — re-peg the credit gate, add active garnishment, and give chosen defaults real credit consequences so the carry pool drains and the economy keeps churning.
type: spec
created: 2026-05-27
last_updated: 2026-05-27
status_note: DRAFT v2 — design synthesis + diagnosis. **THE ACTUAL FIX SHIPPED (2026-05-28) IS NARROWER THAN THIS PLAN.** A cheap A/B sim revealed the live "AIs don't repay" symptom was almost entirely throttling on the existing payoff trigger — baseline fired **0** payoffs in 200 ticks; tuning `PAYOFF_TICK_BASE_RATE 0.005→0.025` + `PAYOFF_AGE_RAMP_DAYS 14→5` took it to **25 payoffs, −2.4% pool drain** (gentle setting). That's the shipped fix. The rest of this plan (gate re-peg / garnish / credit bureau / bank backstop) is **deferred as optional flavor work**, not needed for the validated economic problem. Step 1 (the gate re-peg) was built+tested+sim-validated, then **removed from the tree (2026-05-28)** — not the lever, and not worth carrying as parked flag-gated dead code; design preserved in this doc. See "Resolution" section at the end.
---

# Cash Mode — Credit, Garnishment & Default

> **Read first:** [`CASH_MODE_BACKING_SYSTEM_HANDOFF.md`](CASH_MODE_BACKING_SYSTEM_HANDOFF.md)
> (the stake model, carry semantics, settlement math) and
> [`CASH_MODE_AI_STAKER_INCENTIVES.md`](CASH_MODE_AI_STAKER_INCENTIVES.md)
> (the weighted-matcher scoring this extends).

## TL;DR

AIs accumulate unpayable debt and it never drains. As of 2026-05-27 the live
economy holds **182 outstanding carries / 303,532 chips owed across 93 AIs**,
against 1,666 cleanly settled stakes — and **only 4 defaults have ever
happened**. The carry pool only grows because (1) the credit gate that's
*supposed* to slow over-leveraged borrowers is pegged to the wrong reference
and barely bites at the tiers where the money is, (2) there is no active
repayment drain — carries shrink only via a glacial probability roll, and
(3) the agentic "choose to default" path zeroes the debt but carries no
forward credit consequence, so nothing changes the borrower's behavior.

This spec re-pegs the gate, adds **garnishment of winnings** as the primary
drain, and gives **chosen default** a real credit footprint (rolling default
count + per-staker cooldown), with an optional **bank backstop** so the staker
always has an exit. The design principle: *most debts resolve by garnish while
the borrower is gated down to stakes they can afford; default stays rare and
chosen; the bank is the last resort.*

---

## 1. Diagnosis (with live data)

### 1.1 How an AI gets backed today

Two triggers create a stake: a **peer bailout** (an AI busts → `refresh_table_roster`
converts `forced_leave` into `take_stake` via `find_ai_staker_for`,
`cash_mode/movement.py:617`) and an **aspiration ask** (proactively staking *up*
a tier, gated by `aspiration_bias`). Each candidate staker passes:

- **Hard gates** (`find_ai_staker_for`): staker `willing`; borrower `willing`;
  **capacity** `bankroll × max_loan_pct_of_bankroll ≥ principal` (default
  `max_loan_pct = 0.05`); `respect ≥ respect_floor` (−0.5); `heat ≤ heat_ceiling` (0.7).
- **Soft weighting** among qualifiers (`candidate_weight`, `cash_mode/staker_history.py`):
  wealth-overflow pressure + per-pair belief (`settled +1.0`, open `carry −0.5`,
  `default −1.5`) + relationship warmth.

> **Not "no brakes" — the gap is specifically AI-peer / global credit.** Credit the
> brakes that already exist so we don't rebuild them: the **aspiration** path has a
> `0.5^N` carry-count matcher penalty (`ai_carry_resolution.py:474`) and runs
> event-rate payoff before climbing (`lobby.py:3233`); per-pair default already dings
> staker belief (`staker_history.py`); and the **human/player** offer paths already
> query a recent-default cooldown (`sponsor_offers.py:490`, `player_staking.py:338`).
> What's missing is a brake on the **AI-peer bailout** path and a **borrower-global**
> credit signal. Note also that `find_ai_staker_for` itself has **no borrower credit
> check** — the tier gate is applied by *callers* (`lobby.py:911`), so any caller that
> forgets to wrap the borrower lookup reopens the hole. The gate belongs closer to the
> matcher, or made unmissable.

### 1.2 The credit gate that already exists — and why it doesn't bite

`cash_mode/staking_tier.py` already models **borrower-global** creditworthiness:
`compute_carry_load` sums carry across *all* stakers, and `resolve_tier` buckets
the borrower by `carry_load / max_carry`:

| Tier | Ratio | Effect on offers |
|---|---|---|
| `premium` | < 0.20 | full lender pool, normal cuts |
| `standard` | 0.20–0.60 | low-likability/respect lenders drop; cuts +~7–8% |
| `restricted` | 0.60–1.00 | only high-likability **and** high-respect lenders; cuts +~20% |
| `house_only` | ≥ 1.00 | no personality offers; house archetypes only |

Wired into the bailout path (`lobby.py:911`, over-tier → borrower forced unwilling
→ `forced_leave`), the human-staker path (`player_staking.py:475`), and offer
quality (`sponsor_offers.py` `TIER_RATE_BUMP`).

**The bug: `max_carry` is pegged to the borrower's *current playing stake*
(`10 × min_buy_in @ that tier`), so the debt ceiling scales with the table:**

| Playing tier | Carry cap |
|---|---|
| $2 | 800 |
| $10 | 4,000 |
| $50 | 20,000 |
| $200 | 80,000 |
| $1000 | 400,000 |

Result — the *current* deep debtors, resolved at each stake:

| Borrower | Carry load | @ $2 | @ $50 | @ $200 | @ $1000 |
|---|---|---|---|---|---|
| gordon_ramsay | 63,337 | house_only | house_only | **restricted** | **premium** |
| dr_oz | 12,612 | house_only | restricted | **premium** | premium |
| lucille_ball | 12,607 | house_only | restricted | **premium** | premium |
| wicked_witch | 11,670 | house_only | standard | **premium** | premium |
| (…9 more) | 8–15k | house_only | standard | **premium** | premium |

The gate gives **more rope the more expensive the table.** It only reaches
`house_only` at the cheap tiers — exactly the tiers where nobody is re-staking
these borrowers. At $200, where the debt actually lives (160,688 owed across 33
carries), essentially every debtor is **premium**. So they keep qualifying for
full peer stakes, bust again, and re-borrow. The data confirms the spiral:

- **366 stakes were opened on borrowers who already had an outstanding carry.**
- 52 borrowers hold multiple simultaneous carries; 13 are in an active stake right now while carrying.

### 1.3 No active repayment drain

Carries shrink only through `cash_mode/ai_carry_resolution.py`:
- `try_ai_voluntary_payoff` — per-tick fire `prob = payoff_score × PAYOFF_TICK_BASE_RATE (0.005)`,
  where `payoff_score` ramps on debt **age over 14 days** (`PAYOFF_AGE_RAMP_DAYS`) and
  staker **heat**. The high-rate path (`PAYOFF_EVENT_BASE_RATE = 1.0`) only fires on
  *events* — climbing a tier or leaving with profit — which broke debtors don't do.
- `garnished_stake_cut` (`movement.py:746`) bumps a re-stake's cut **only when the
  same staker re-backs the same borrower**, cap +20pp (`GARNISHMENT_RATE_CAP`),
  ceiling 0.55. It does nothing for debts owed to a staker who isn't re-backing them.

Net: **all 182 carries are < 4 days old; none have aged into meaningful payoff
pressure; zero have been repaid down.** The drain rate is ~0.

### 1.4 Default is consequence-free for credit

`try_ai_explicit_default` already models default as an **agentic choice** under
pressure (`DEFAULT_PRESSURE_THRESHOLD = 0.6`; pressure from `bankroll_factor < 0.5`,
`energy < 0.3`, staker `respect < −0.2`, oldest-debt weight). On default it zeroes
the carry, flips status to `defaulted`, and fires a relationship hit. The human/player
offer paths *do* read a recent-default cooldown — but on the **AI-peer** path there is
**no cooldown, no rolling default count, and the tier gate ignores default history
entirely** (it's carry-load only). An AI defaulter is fully re-backable by another AI
the next tick.

---

## 2. Design

Four parts. The principle: garnish drains productive debtors automatically while
the credit gate holds them at affordable stakes; default is the rare chosen exit
with a lasting mark; the bank is the staker's backstop.

### Part A — Re-peg the credit gate (the leak fix)

**A1. Change the carry-cap denominator** from "current playing stake" to a
borrower-intrinsic reference so debt is measured against *ability to pay*, not the
table they happen to sit at. **Formula (proposed defaults 2026-05-27):**

```
max_carry       = max(starting_bankroll, current_chips) × CREDIT_MULTIPLIER   # CREDIT_MULTIPLIER = 1.0
default_penalty = recent_default_count × 0.5 × starting_bankroll
cap             = max(entry_tier_cap, max_carry − default_penalty)
```

- **`max(starting_bankroll, current_chips)`** — not seed alone. Rewards winners (an AI
  who grew 10k→100k is judged on 100k → more rope) while the `starting_bankroll` floor
  means losers fall back to *baseline* rope, never stranded below it. Uses **gross chips,
  not net-of-debt**, to avoid circularity (carry must not feed the cap that gates carry).
  This resolves the upward-mobility flag from review without the "broke = permanently
  house_only" failure of a pure current-bankroll peg.
- **`CREDIT_MULTIPLIER = 1.0`** → the story is "you go `house_only` once you owe more
  than you're worth," keeping the existing 0.20/0.60/1.00 thresholds meaningful as "owe
  <20% / 20–60% / 60–100% / >100% of your worth." **Sim-tune** — loosen toward 1.5 if it
  strands too many.
- **`entry_tier_cap` floor** (the $2 cap, 800) guarantees every borrower always has
  *some* low-tier headroom so the grind-back path is real (Part C double-penalty caution).

> **Sim correction (2026-05-27): take the MIN of legacy and worth caps, not the worth
> cap alone.** A paired within-run probe (see `reference_cash_sim_ab_paired`) found the
> worth cap *alone* nets **looser** at real bailouts — 28 evals, 13 looser / 1 stricter /
> 0 newly-blocked — because the legacy `10×min_buy_in@stake` cap is generous at HIGH
> tiers (the leak) but *strict at LOW tiers* (800 @ $2), and most bailouts happen at low
> tiers. So the gate uses `combined_carry_cap = min(max_carry_for_tier(stake),
> borrower_credit_cap(...))` — strictest of both. It can only ever tighten vs legacy:
> preserves low-tier discipline AND closes the high-tier leak (a deep-debt borrower's
> worth cap bites where the generous high-tier stake cap doesn't). `CREDIT_MULTIPLIER`
> becomes far less twitchy — at low tiers legacy dominates the min, so the worth term
> only acts where legacy was too loose.

Worked against live debtors who are `premium` at $200 today: gordon (63,337 / 45,000 =
1.41) → **house_only** everywhere; agatha (7,988 / 22,000 = 0.36) → **standard**; a
median 10k AI with one $50 carry (1,047 / 10,000 = 0.10) → stays **premium**. The gate
bites the over-leveraged and leaves small carries alone.

**A2. Fold a recent-default component into the tier**, so credit = `f(carry_load,
recent_default_count)` rather than carry-load alone. A borrower with 2+ defaults in
the trailing window is gated harder even if their current carry is small.

**A3. Close the high-tier / house leak.** Verify that `house_only` doesn't simply
reroute the borrower to freely-available house stakes at the same volume (the gate
must *slow* re-staking, not redirect it). Allow the cheap end ($2) to stay open so
gated borrowers can grind back — the gate funnels *down*, it doesn't freeze them out.

### Part B — Garnishment of winnings (the drain)

While a borrower carries any debt, **skim a fraction of their winnings toward the
oldest carry automatically** — no probability roll. This is the workhorse that makes
the 14-day voluntary-payoff ramp irrelevant (per Jeff: "garnish until forgiven makes
the 14 days ok"). It extends, not replaces, `garnished_stake_cut` (which stays as the
re-stake-cut bump for the same-staker case).

**B1. Leave-time garnishment only (first cut). Not per-hand.** Per-hand garnish mutates
a live table stack, perturbs future poker outcomes, and tangles with active-stake math.
Leave-time is far easier to reason about and test. The hook is *after*
`settle_stake_on_leave` (`stake_settlement.py:160`) has computed the **current** stake's
normal settlement (staker recovery/cut, borrower return, or a new carry).

**B2. Garnish only the borrower's post-settlement surplus — never gross winnings.**
Skimming gross table winnings would let an *old* creditor take chips that contractually
belong to the *current* staker. The contract:

```
garnishable = max(0, settlement.borrower_total − reseat_buffer)
payment     = min(oldest_carry.carry_amount, int(garnishable × GARNISH_RATE), abs_cap)
```

- **No garnish when the current stake itself ends in `carry`** — there is no borrower
  surplus to skim.
- `reseat_buffer` leaves the borrower enough to sit *somewhere* (mirrors the
  affordability floor in `try_ai_voluntary_payoff`) so garnish never strands them.
- Reuse the `GARNISHMENT_RATE_CAP` / `GARNISHMENT_ABSOLUTE_CAP` calibration for `GARNISH_RATE`/`abs_cap` consistency.

**B3. The transaction contract (this is the part that must not be vague).** Garnish is
a **bankroll-to-bankroll transfer** and must follow the *exact* accounting
`try_ai_voluntary_payoff` already uses (`ai_carry_resolution.py`), not invent new flows:

| Step | Rule |
|---|---|
| Debit | borrower bankroll −= `payment` |
| Credit | staker bankroll += `payment` |
| **Human staker** | credit lands on `player_bankroll_state`; **pre-flight that the row exists before debiting** the AI (never vaporize chips on a missing staker) |
| **Projection/regen** | if writing a projected balance, record an `ai_regen` ledger entry exactly as payoff does (materializing regen silently breaks audit) |
| Carry | `carry_amount −= payment`; full clear → `status='settled'` |
| P&L history | `staker_payout += payment`, `borrower_payout −= payment` (omitting this makes net-worth / P&L surfaces diverge from real chips) |
| Ordering | apply garnish strictly **after** the current staker is credited, so the current staker is never shorted |
| Ledger | **do not** add chip-ledger entries for ordinary AI↔AI / AI↔human transfers — `settle_stake_on_leave` (`stake_settlement.py:125`) deliberately routes non-house flows bankroll-to-bankroll *off* the ledger; mismatched entries manufacture `drift`. The ledger is for house/central-bank flows only. |

**B4. Relationship & surfacing.** A *partial* garnishment fires **no** relationship
event (avoid `STAKE_REPAID` spam / boost farming). Only a **full clear** fires one
positive `STAKE_REPAID`. Surface garnishment on the recent-events buffer (§3.3) for
visibility without a relationship side effect.

### Part C — Chosen default with credit consequences

Keep default **agentic** — `try_ai_explicit_default` stays the trigger (an AI under
sustained pressure *chooses* to walk; being underwater is one input, not the whole
trigger). Add the forward consequences:

- **Rolling default count** — defaults stamped with a timestamp; **defaults in the
  trailing 7 days** (matching the existing human-path cooldown) is the credit signal. It
  feeds the A1 cap via `default_penalty = recent_default_count × 0.5 × starting_bankroll`
  — so **2 defaults in a week ≈ wipes credit to `house_only`** for anyone carrying real
  debt — and also dings the matcher belief score.
- **Per-staker cooldown** — defaulting on staker X blocks any new stake *from X* for
  7 days. This already exists for human/player stakers (`_has_recent_default_to` /
  `recent_default` reason in `player_staking.py:326` + `sponsor_offers.py:485`); the
  work is **generalizing that same cooldown to the AI-peer bailout path**, not building
  it fresh.
- **Stake-quality degradation** — a higher default count pushes the borrower toward
  `restricted`/`house_only` and worsens offered terms, so it's *harder to find a
  backer whose terms the AI will accept* → they slide to $2 / side hustle to earn
  back in (per Jeff's fork-2 description).
- **Surface it** — "🏳️ defaulted on Napoleon" on the recent-events ring buffer
  (`recent_events_json`, schema v117) for the lobby/dossier.

> **Double-penalty caution (from review).** The re-pegged gate (A) + garnish drain (B)
> + default-count penalty (C) now *stack on top of* the existing per-pair belief/default
> weights in `staker_history.py`. Without a real cheap-tier escape this makes debtors
> **permanently unrecoverable**. The `entry_tier_cap` floor in A1 and a genuinely-open
> $2 / side-hustle path (§4 decision 3) are load-bearing, not nice-to-haves — verify in
> sim that a gated debtor can actually climb back out.

### Part D — Bank backstop / sell-to-bank (optional, later, staker-initiated)

A manual escape valve, *not* automatic. The staker flags a carry as unrecoverable
and **sells it to the bank** for a recovery payout larger than the expected
hold-and-garnish value.

- Selling **records the default** on the borrower's credit record (the write-off
  *is* the default event for credit purposes). Because the borrower didn't *choose* to
  burn this staker, the sale must **not** emit the normal chosen-default relationship
  hit against the staker — credit consequence yes, interpersonal resentment no.

**This is real central-bank issuance and needs an explicit accounting model before any
code** (the "print + burn" phrasing is otherwise a hand-wave that will leak chips):

| Ledger reason | Flow | Sign |
|---|---|---|
| `bank_debt_purchase_issue` | central bank → staker (recovery payout) | + issuance |
| `bank_sterilization_burn` | rake/surplus bucket → burn | − issuance (matched or bounded) |

- **Carry ownership must transfer** (original staker → bank) or the carry must be closed
  and a *new bank-owned receivable* created. Otherwise a later repayment/garnish credits
  **both** the original staker and the bank — double credit, inflation.
- **Audit invariant:** `purchase_issuance − sterilization_burn + remaining_bank_receivable`
  must be fully explainable. If the print succeeds but the burn fails → inflation; if the
  burn hits already-counted surplus → deflation. Order and idempotency matter.

Sequenced **last** and independent of A–C.

---

## 3. Data model

> Confirm exact schema-version numbers against the live migration head before
> implementing. Prefer additive, PRAGMA-guarded columns (the v117 pattern).

### 3.1 Default history
Reuse `stakes.status='defaulted'` + `settled_at` for the rolling count, **or** add a
dedicated `default_events(borrower_id, staker_id, amount, defaulted_at)` table if we
want default history to survive stake-row cleanup and to drive cooldowns cleanly.
(Decision pending — §4.)

### 3.2 Garnishment accounting (NOT a ledger flow)
Correction from review: garnishment between two *non-house* parties is a
**bankroll-to-bankroll transfer and must stay off the chip ledger** — `settle_stake_on_leave`
(`stake_settlement.py:125`) already routes non-house stake flows this way, and the chip
ledger is reserved for house/central-bank issuance. Adding ledger rows for AI↔AI / AI↔human
garnish would *manufacture* `drift`, not preserve it. Conservation is held by the
balanced debit/credit + `staker_payout`/`borrower_payout` updates in B3, plus the
`ai_regen` entry *only* when a projected balance is materialized. The one place a new
ledger reason **is** required is the **bank backstop** (§Part D), which is genuine
central-bank issuance.

### 3.3 Surfacing
Push `defaulted` / `garnished` events to `recent_events_json` for the lobby drawer
and dossier.

---

## 4. Proposed defaults (set 2026-05-27)

Calibrated against live data (median seed bankroll 10k; the old `10× min_buy_in` cap;
real carry sizes). "Sim-tune" = expect the economy sim to move it.

| # | Knob | Default | Basis |
|---|---|---|---|
| 1 | `CREDIT_MULTIPLIER` (A1) | **1.0** on `max(seed, current_chips)` | "owe > your worth → house_only"; median seed 10k. **Sim-tune** (→1.5 if over-gating). |
| 1b | earned-credit term | folded into the `max(seed, current)` denominator | winners judged on current chips; no separate term for v1. |
| 2 | `GARNISH_RATE` (B) | **0.50** of post-buffer surplus | "half your profit clears the debt"; ~3 winning sessions to clear an 8k carry. **Sim-tune**. |
| 2b | `reseat_buffer` (B) | **1 × min-buy-in at current tier** | never strand the borrower out of a seat. |
| 3 | Gate hardness | **hard-block personality stakes at `house_only`; $2 + side-hustle always open** | the seed-floor $2 cap is the load-bearing recovery valve (Part C caution). |
| 4 | Default window / penalty | **7-day window**; `penalty = count × 0.5 × seed` | matches the existing human cooldown; 2 defaults ≈ house_only. |
| 5 | `try_ai_explicit_default` | **unchanged (0.6 / 0.10); measure first** | garnish should shrink the drowning pool; re-tune only if default rate stays high. |
| 6 | Bank recovery ratio (D) | **0.50 of carry face** | real recovery vs ~0; haircut bounds inflation; burn sterilizes the printed half. |

Genuinely still-open (all in D, deferred): whether the bank keeps garnishing post-purchase
vs fully writes off, and the exact burn/sterilization source bucket.

---

## 5. Build sequence

The data says the **gate re-peg (A) + garnishment (B)** are the highest-leverage
fixes — they stop the spiral and drain the existing 303k pool. Order (adjusted per
review — small matching-signal fixes land before the conservation-sensitive drain):

1. **A1 + A2** — re-peg the carry-cap denominator (`starting_bankroll` + floor) and fold
   in default count. Smallest diff, biggest behavioral change; unit-testable against
   `resolve_tier`. **Do this first.**
2. **C (matching half)** — the missing AI-peer default consequences that affect *future
   matching*: rolling default count + AI-peer recent-default cooldown feeding the gate
   and belief score. Small, and closes the AI-peer hole the gate left.
3. **B** — leave-time garnishment with the B3 transaction contract. **Conservation
   tests are the gate here** — assert `drift = 0` and P&L consistency before merge.
4. **Recalibrate `try_ai_explicit_default`** — now that a real drain exists, the desired
   default rate likely drops; re-tune the 0.6/0.10 trigger.
5. **D** — bank backstop / sell-to-bank (later, independent, accounting-heavy).

---

## 6. Validation (sim + live)

Run `cash_mode/sim_runner.py` over a multi-day horizon and assert:
- **Re-stakes on already-indebted borrowers drop sharply** (today: 366).
- **Carry pool drains** rather than monotonically growing; median carry age stops
  pinning at "everything < 4 days."
- **Deep debtors actually slide tiers** (gordon-class resolves restricted/house_only
  at every stake, not premium at $200/$1000).
- **Default rate stays low** — garnish should resolve most debt; default is the tail.
- **Chip conservation `drift = 0`** across garnishment and (if built) the bank
  print+burn.
- Spot-check that a productive debtor climbs back out (garnish → settled → eligible)
  and an unproductive one slides to $2 / side hustle rather than spiraling.

---

## 7. Resolution (2026-05-28)

A step-back review halfway through Step 1 revealed the plan was over-built relative to
the validated problem. A cheap A/B sim (`scripts/sim_experiments/exp_payoff_tuning.py`)
showed:

| arm | tick rate | ramp days | payoff fires (200 ticks) | carry-pool drain |
|---|---|---|---|---|
| baseline | 0.005 | 14 | **0** | +275 (flat) |
| gentle | **0.025** | **5** | **25** | −11,909 |
| tuned | 0.05 | 3 | 66 | −39,157 |

Baseline reproduced the live "AIs don't repay" symptom (zero payoffs). **Gentle —
two constants — fixed it.** Shipped:

```python
# cash_mode/ai_carry_resolution.py
PAYOFF_TICK_BASE_RATE = 0.025   # was 0.005
PAYOFF_AGE_RAMP_DAYS  = 5.0     # was 14.0
```

Tests updated (`test_carry_age_factor` parametric on the ramp constant); carry-resolution
suite green. No other day-windows needed to move — the 7-day cluster (`FORGIVENESS_RATE_LIMIT_SECONDS`,
`RECENT_DEFAULT_WINDOW_DAYS`, `LENDER_DEFAULT_COOLDOWN_SECONDS`,
`PLAYER_STAKE_DEFAULT_COOLDOWN_SECONDS`) governs "credit/relationship memory of a bad event,"
which is semantically orthogonal to "how fast debt-age presses the borrower."

**Status of the four-part plan:**
- **A (gate re-peg)** — built + tested + sim-validated, then **removed from the tree (2026-05-28)** rather than left as parked flag-gated dead code. Sim showed it's *not the lever*; the design (incl. `combined_carry_cap = min(legacy, worth)` and the worked examples) is preserved in §2 / §Part A above. Rebuild from this doc only if a sim ever shows the high-tier leak actually bites — and re-validate calibration then.
- **B (leave-time garnish)** — deferred; the tuning fix removes the urgency.
- **C (credit bureau / chosen-default consequences)** — deferred; narrative feature, not economic necessity.
- **D (sell-to-bank backstop)** — deferred; optional flavor.

**Lesson banked to memory:** the cash sim's same-seed A/B is RNG-desync-confounded for
decision-gate changes — use a paired within-run probe (see
[`reference_cash_sim_ab_paired`](../../../home/jeffh/.claude/projects/-home-jeffh-projects-my-poker-face/memory/reference_cash_sim_ab_paired.md)).
