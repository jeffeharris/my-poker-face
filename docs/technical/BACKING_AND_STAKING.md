---
purpose: Architecture of the cash-mode backing/staking economy — how stakes are offered, accepted, carried, and settled, and how they touch the chip ledger
type: architecture
created: 2026-06-03
last_updated: 2026-06-04
---

# Backing & Staking

When a player (or an AI) can't afford a seat, someone else can put up the chips.
The unit of the system is the **stake**: one deal struck at sit-down and settled at
leave-table. The staker funds the `principal`; the borrower plays it; at session end
the chips are split per the agreed `cut`. If the borrower busted before recovering
the principal, the residual becomes a **carry** — a static debt that follows the
borrower until they work it down, get it forgiven, or default on it.

This doc maps the durable structure: the data model, the offer→accept→carry→settle
lifecycle, the settlement math kernel, the sponsor-offer generators, and the
(deliberately narrow) chip-ledger seam. For the chip-conservation invariant this
system must not break, see [`CHIP_CUSTODY_LEDGER.md`](CHIP_CUSTODY_LEDGER.md). For
how stakes fit the broader closed economy (pool, rake, vice), see
[`CASH_MODE_ECONOMY.md`](CASH_MODE_ECONOMY.md).

> The "why" notes attributed to plan/log sources below are **point-in-time design
> intent**, not code-verified behavior. Where a source conflicts with the code, the
> code wins and is cited.

---

## 1. Data model

### `stakes` table (schema v98)

The `Stake` frozen dataclass — one row per session's deal — is defined at
`cash_mode/stakes.py:50`. String-literal enums (not `Enum`) because the values cross
the DB boundary as TEXT and the audit/lobby paths read them raw
(`cash_mode/stakes.py:23-47`). Persistence + DDL live in
`poker/repositories/stake_repository.py` ("v98 `stakes` persistence surface",
line 1) with the table created via `poker/repositories/schema_manager.py`.

| Field | Meaning |
|-------|---------|
| `staker_id` | NULL for house; `personality_id` or `owner_id` otherwise (repo maps `None`↔NULL) |
| `staker_kind` | `house` \| `personality` \| `human` — drives settlement routing |
| `borrower_kind` | `human` (Phase 1) \| `personality` (Phase 4) |
| `format` | `pure` \| `match_share` \| `house` |
| `principal` | chips the staker put up |
| `match_amount` | chips the borrower put up (`match_share` only; else 0) |
| `origination_fee` | borrower → staker bankroll at sit-down (`pure` only) |
| `cut` | staker's fraction `[0.0, 1.0]` of net winnings |
| `status` | `active` → `settled` \| `carry` \| `defaulted` |
| `carry_amount` | residual principal owed when `status='carry'`; else 0 |
| `stake_tier` | STAKES_LADDER key (`$2`, `$10`, …) the stake was made at |
| `forgiveness_last_asked` | rate-limit stamp for `/request-forgiveness` |
| `staker_payout` / `borrower_payout` | v106 — chips returned each side, for Net Worth P&L; NULL on active/legacy rows |
| `pending_forgiveness_ask` | v110 — set when an AI borrower awaits a *human* staker's grant/refuse |
| `table_id` | v111 — lobby table the stake opened against; NULL on AI↔AI + legacy rows. Purely additive; settlement stays keyed on `session_id` |
| `resolution` | v150 — display label for *how* a closed stake resolved when `status` alone isn't specific. `'bankruptcy'` for carries discharged by the insolvency valve (§4); NULL for ordinary settle/default. Status stays `defaulted`, so default-counting consumers are unaffected — this only drives the Net Worth history badge |

Status constants: `STAKE_STATUS_ACTIVE`/`SETTLED`/`CARRY`/`DEFAULTED`
(`cash_mode/stakes.py:44-47`).

### The three formats

- **`pure`** — staker funds the full principal; borrower pays an `origination_fee` up
  front; staker takes `cut` of upside.
- **`match_share`** — both sides contribute; no fee; higher `cut`. `match_amount` is
  the borrower's contribution.
- **`house`** — lender of last resort. The house never carries: on a bust the math is
  overridden to `settled` and the balance is forgiven (see §4).

(Format semantics: `cash_mode/stakes.py:58-93`.)

---

## 2. Profiles — who lends and who borrows

Every personality carries two sub-dicts in `personalities.config_json`, alongside
`bankroll_knobs` and `anchors`.

### `staker_profile` — how they lend

`StakerProfile` dataclass at `cash_mode/staker_profile.py` (frozen — relationship
adjustments produce a fresh offer rather than mutating the profile). Loaded by
`BankrollRepository.load_staker_profile(personality_id)`, per-field fallback to
`STAKER_PROFILE_DEFAULTS`.

| Field | Default | Role |
|-------|---------|------|
| `willing` | `True` | lends at all? |
| `max_loan_pct_of_bankroll` | `0.05` | per-loan cap vs. their bankroll |
| `floor_anchor` | `1.20` | base repayment-floor before relationship adjustment |
| `rate_anchor` | `0.30` | base cut |
| `respect_floor` | `-0.5` | won't lend below this respect |
| `heat_ceiling` | `0.7` | won't lend above this heat |

Defaults at `cash_mode/staker_profile.py:54` (`STAKER_PROFILE_DEFAULTS`).

### `borrower_profile` — how they borrow

`BorrowerProfile` dataclass at `cash_mode/staker_profile.py:67`. Loaded by
`BankrollRepository.load_borrower_profile(personality_id)`. Stoic/principled
personalities (Lincoln, Buddha-class) set `willing=False`; the Phase 4 take-stake
path checks this directly.

Distinguishing trait: when `willingness_threshold` (and `aspiration_bias`,
`payoff_eagerness`) are absent from the sub-dict, they are **derived from anchors**
rather than defaulted — `willingness_threshold` from `anchors.ego`, the others from
ego + risk_identity / poise combinations. This keeps a personality's lending/borrowing
disposition coherent with its psychology even when the staking fields were never
hand-authored.

---

## 3. Offer generation

### 3a. House (anonymous archetype) offers

`compute_offers_for_table(min_buy_in, max_buy_in, count=3)` (`cash_mode/sponsor_offers.py:157`)
samples from six `_Archetype` objects (`SPONSOR_ARCHETYPES`,
`cash_mode/sponsor_offers.py:85`). Each encodes an `amount_fn`, a repayment `floor`,
and a `rate` (the staker's cut), clamped to `[min_buy_in, max_buy_in]` by
`_materialize` (`cash_mode/sponsor_offers.py:137`). The server re-materializes from
`archetype_id` on acceptance — no client-side trust.

| Archetype | Amount | floor | rate |
|-----------|--------|-------|------|
| `friendly_boost` | `min` | 1.00 | 0.20 |
| `square_deal` | `min×1.5` | 1.10 | 0.25 |
| `the_premium` | `max×0.5` | 1.30 | 0.00 |
| `skin_in_the_game` | `max×0.7` | 1.15 | 0.15 |
| `whale_backer` | `max` | 1.00 | 0.50 |
| `loan_shark` | `max×0.8` | 1.30 | 0.40 |

### 3b. Personality (named AI) offers

`compute_personality_offers(...)` (`cash_mode/sponsor_offers.py`) filters candidates
through stacked gates — `willing`, capacity (`max_loan_pct × bankroll ≥ min_buy_in`),
`respect ≥ respect_floor`, `heat ≤ heat_ceiling`, the Phase-2 tier floors (§5), a
7-day default cooldown, and the player-prestige hook (`human_regard ≤ VILLAIN_REGARD_FLOOR`
closes the whole personality pool). Surviving candidates get relationship-adjusted
terms (likability/heat/respect nudge floor & rate), a tier rate bump, and per-staker
carry garnishment, sorted by capacity descending.

### 3c. Player-offered stakes to AIs (Phase 5)

`list_stakeable_ai(...)` (`cash_mode/player_staking.py:373`) lists eligible AIs;
`evaluate_player_offer(...)` (`cash_mode/player_staking.py:548`) runs the AI's
accept/refuse decision. The math (constants at `cash_mode/player_staking.py:90-101`):

```
score             = likability×0.5 + respect×0.4 − heat×0.3     (_relationship_score, :250)
cut_penalty       = max(0, cut − 0.30) × 2.0                    (FAIR_CUT_REFERENCE, CUT_PENALTY_SLOPE)
desperation       = ego × max(0, 1 − current/starting)          (_compute_desperation, :227, clamp [0,1])
threshold         = willingness_threshold + cut_penalty − desperation×0.4   (DESPERATION_RELIEF)
accept            ⇔ score > threshold
```

Predatory cuts (>30%) raise the bar; the multiplicative `desperation` means only a
*proud-and-broke* AI (high ego, low chips) lowers it — a content low-ego AI never
gets desperate.

---

## 4. Lifecycle: accept → carry → settle

### Accept (sit-down)

`POST /api/cash/sponsor-and-sit` (`flask_app/routes/cash_routes.py`). The principal is
passed as `player_starting_stack` straight into the table stack — it **never lands in
the borrower's bankroll**, which closes the "pocket the loan and leave" exploit by
construction. The stake row is created `status='active'`. A house stake fires the only
stake-creation ledger entry (§6); a personality stake fires a `STAKE_OFFERED`
relationship event.

### Carry (cross-session debt)

A stake is `active` for exactly one session. If the session ends before the principal
is recovered, the shortfall becomes `carry_amount` and `status='carry'`. **Carries are
static — no interest accrues.** Per the backing-system handoff
(`docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md`, 2026-05-19), time-based interest was
rejected as punishing players for taking a break; the structural brake is instead the
**carry cap** (§5).

### Settle (leave-table)

`settle_stake_on_leave(stake_id, chips_at_leave, …)` (`cash_mode/stake_settlement.py:95`)
delegates to the pure-math kernel `_compute_chip_flows(stake, chips_at_leave)`
(`cash_mode/stake_settlement.py:288`). `invested = principal + match_amount`:

| Case | Condition | staker_total | borrower_total | status | carry |
|------|-----------|--------------|----------------|--------|-------|
| **Full bust** | `chips_at_leave ≤ 0` | 0 | 0 | `carry` | `principal` |
| **Clean settle** | `net_winnings ≥ 0` (`chips_at_leave ≥ invested`) | `principal + ⌊cut×net⌋` | `match_amount + (net − staker_cut)` | `settled` | 0 |
| **Partial carry** | else (`0 < chips_at_leave < invested`) | `min(chips_at_leave, principal)` | `max(0, chips − staker_total)` | `carry` if `carry > 0` else `settled` | `principal − staker_total` |

where `net_winnings = chips_at_leave − invested`. The staker's principal is the
**protected layer** — on a partial bust they recover first, the borrower gets the
remainder.

When the partial-bust path leaves `carry_amount == 0` — the staker fully recovered
principal and only the *borrower's* own match took the loss — the kernel returns
`settled`, not a `$0` carry (`cash_mode/stake_settlement.py:340`). Only `match_share`
stakes reach this (a `pure` stake busting below `invested` always has
`chips_at_leave < principal`, so `carry > 0`). Without the clean-settle, a zeroed
carry surfaced as a phantom "$0 owed" receivable.

**House override**: when `staker_kind='house'` and the math returns `carry`, the
result is forced to `settled` with the balance forgiven. This override lives in the
caller `settle_stake_on_leave` (`cash_mode/stake_settlement.py:164-184`), not the pure
kernel — `_compute_chip_flows` returns `carry` for a bust and the caller flips house
rows to `settled` (carry_amount=0) and fires a `forgive_balance` annotation for the
unrecovered principal. The house is the lender of last resort and never carries
(intent: `cash_mode/stakes.py:58-67`; format `house`).

The kernel returns a pure result object; the caller (`_leave_table_locked` in
`cash_routes.py`) walks the chip-flow directions and dispatches the actual transfers
(borrower-seat → staker bankroll, borrower-seat → house ledger, or
borrower-seat → borrower bankroll).

### Relationship events at settle

Axis shifts (staker's point of view) at `poker/memory/relationship_events.py:219-231`:

| Event | heat | respect | likability |
|-------|------|---------|------------|
| `STAKE_OFFERED` | 0.00 | +0.05 | +0.03 |
| `STAKE_REPAID` | −0.05 | +0.15 | +0.10 |
| `STAKE_DEFAULTED` | +0.30 | −0.30 | −0.20 |
| `STAKE_FORGIVEN` | −0.10 | +0.05 | +0.05 |
| `STAKE_FORGIVENESS_REFUSED` | (small negative — sub-default) | | |

A *natural* carry fires no event — the debt just persists. A second axis table
(`relationship_events.py:319`) carries the mirrored/recipient-side shifts. Event enum
values at `relationship_events.py:96-100`.

### Five ways out of a carry

Per the handoff (locked decision #12), the first four paths **never force a bankroll
payment as a precondition to play** — the mechanic is reputation-driven, not a payment
wall. The fifth (bankruptcy) is the involuntary terminal valve for a borrower who can't
use any of the others:

1. **Voluntary payoff** — debit bankroll → staker. AI-side: `try_ai_voluntary_payoff`.
   Targets the carry whose staker the borrower most *prefers* (highest
   borrower→staker likability, oldest as tiebreak), not strict-oldest — "pay the people
   you like first" (`_select_payoff_target`, `cash_mode/ai_carry_resolution.py:545`).
2. **Garnishment** — a slice of a *new* stake's winnings, applied as a rate bump on the
   same staker's next offer.
3. **Forgiveness** — ask via relationship axes (`/request-forgiveness`); rate-limited.
4. **Explicit default** — clears the carry, takes the reputation hit (`STAKE_DEFAULTED`).
   AI-side fires only under sustained pressure (≥ `0.6`), so a positive-relationship
   carry can't reach it.
5. **Bankruptcy** — the terminal valve (`try_ai_bankruptcy`,
   `cash_mode/ai_carry_resolution.py:1254`). Fires when an AI borrower's *oldest* carry
   is past `BANKRUPTCY_TIMER_DAYS` (7) **and** they're insolvent (liquid bankroll < total
   outstanding carries). Liquidates the bankroll pro-rata across *all* creditors
   (rounding remainder to the largest carry — no chip destruction), defaults the
   remainder of every carry, zeroes the bankroll, and records the event. Resolves first
   in `resolve_ai_carries`, so a bankrupt AI skips the other paths that tick. The
   chips→0 outcome feeds the existing side-hustle recovery loop next refresh. No free
   pass either side: the staker recovers only what's there (real downside risk), the AI
   eats a `STAKE_DEFAULTED` per creditor + a recorded `bankruptcy_count`.

**Bankruptcy credit history (per-sandbox).** `ai_bankroll_state.bankruptcy_count` +
`last_bankruptcy_at` (schema v149) record the consequence. They drive a **post-bankruptcy
loan-term penalty**: a recently-bankrupt borrower's `take_stake` cut is bumped
(`bankruptcy_penalized_cut`, `cash_mode/movement.py:1057`) — a credit-score ding, not a
lockout — that **decays linearly to 0 over `BANKRUPTCY_PENALTY_DECAY_DAYS` (30)**, the v1
time-decay redemption. Stacks on the garnishment bump under the shared
`GARNISHMENT_ABSOLUTE_CAP` (0.55). The lifetime count surfaces on the character dossier
as a gated **credit-history** section (free to a staker the borrower has personally
defaulted on; otherwise an informant purchase) — `flask_app/routes/character_routes.py`,
`stake_repo.has_defaulted_stake`.

---

## 5. Tiers — the over-leverage brake

`resolve_tier(...)` (`cash_mode/staking_tier.py`) buckets a borrower by
`carry_load = Σ carry_amount` against `max_carry = CARRY_CAP_MULTIPLIER × min_buy_in @
current tier` (locked decision #8 — "`10 × min_buy_in @ current tier`",
`CARRY_CAP_MULTIPLIER=10` at `staking_tier.py:54`, `max_carry_for_tier` at `:74`):

| Tier | `ratio = carry_load/max_carry` | Effect |
|------|-------------------------------|--------|
| `premium` | `< 0.20` | full lender pool, normal cuts |
| `standard` | `0.20 – 0.60` | low-likability/respect lenders drop; +rate bump |
| `restricted` | `0.60 – 1.00` | only high-likability AND high-respect lenders |
| `house_only` | `≥ 1.00` | no personality offers — house archetypes only |

Threshold constants: `THRESHOLD_PREMIUM_TO_STANDARD=0.20`,
`THRESHOLD_STANDARD_TO_RESTRICTED=0.60`,
`THRESHOLD_RESTRICTED_TO_HOUSE_ONLY=1.00` (`staking_tier.py:48-50`). An unknown stake
label or over-cap load fails safe to `house_only` (`staking_tier.py:111-124`). Tier
constants `TIER_PREMIUM`/`STANDARD`/`RESTRICTED`/`HOUSE_ONLY` at `staking_tier.py:33-36`.

The path back from over-leverage is "grind out at bad terms" — high-cut house stakes,
slowly paying down carry, re-qualifying for personality offers as the ratio falls.

---

## 6. Chip-ledger interaction (deliberately narrow)

**Only house stakes touch the chip ledger.** Personality and human stakes move
bankroll-to-bankroll with no central-bank counterparty, and the chip-custody audit's
`actual_outstanding` already counts both sides of those transfers (chips in bankrolls +
chips in seats), so conservation holds without a ledger entry (handoff, Phase-1
chip-ledger section). Carry creation itself fires no ledger entry — the chips are
already in the universe; `carry_amount` is a tracking field, not a chip claim.

| Event | Ledger call | Site |
|-------|-------------|------|
| Human accepts house stake | `record_house_stake_issue` | `cash_routes.py:2295` |
| House stake settles / busts | `record_house_stake_settle` | `cash_routes.py:4737` |

Ledger methods at `core/economy/ledger.py:940` (`record_house_stake_issue`) and `:999`
(`record_house_stake_settle`), imported into `cash_routes.py:70` as `chip_ledger`.

> **Invariant to preserve.** If a future change routes personality/human stakes through
> the ledger, or makes carry a ledger-tracked claim, the audit's `actual_outstanding`
> term must change in lockstep or chip conservation will appear to break. See
> [`CHIP_CUSTODY_LEDGER.md`](CHIP_CUSTODY_LEDGER.md).

---

## 7. Design rationale (sourced, not code-verified)

From `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` (2026-05-19) and related logs.
These capture intent at the time; the code above is the authority.

- **Stakes, not loans.** The principal physically leaves the staker's bankroll at deal
  time, so a borrower's loss does not propagate back up a chain — there is no "default
  cascade." The staker's exposure is bounded at the principal. The Phase-2 implementer
  was explicitly warned *not* to "fix" the cheap-stake/win-big/default leverage play by
  gating default on bankroll — that was rejected in design.
- **Carry is static.** Interest would punish players for stepping away; the carry cap
  is the structural brake instead.
- **Forgiveness forces no payment.** Locked decision #12: all four exit paths keep the
  mechanic about relationships and reputation, never a payment wall.
- **The v1 `floor` knob was collapsed into `cut`.** Legacy `offer_floor` in
  logs/responses is a display artifact and no longer affects settlement math (handoff;
  `sponsor_and_sit` comment).
- **Ledger tracks only house stakes** because only they have a central-bank
  counterparty; peer stakes are bankroll-to-bankroll and already conserved by the audit.

---

## See also

- [`CHIP_CUSTODY_LEDGER.md`](CHIP_CUSTODY_LEDGER.md) — the chip-conservation invariant
  this system must not break; house-stake ledger entries.
- [`CASH_MODE_ECONOMY.md`](CASH_MODE_ECONOMY.md) — the closed economy the stake pool
  lives inside (pool, rake, vice, wealth levers).
- `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` — the source design doc (phases 1–5,
  locked decisions).
