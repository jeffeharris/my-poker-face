---
purpose: Implementation handoff for the staking system — session-based stakes with light carry, reputation-driven offer quality, AI and human as borrowers and stakers. Replaces Path B's session-scoped sponsorship model.
type: guide
created: 2026-05-19
last_updated: 2026-05-21
status_note: Phase 4.5 + Phase 5 shipped 2026-05-21. AI-initiated payoff / forgiveness / explicit-default behaviors, garnishment + tier-gate on AI take_stake, and the player-as-staker route + UI are live. 327 cash_mode tests passing.
---

# Cash Mode — Backing System Handoff

> **Read first:** [`CASH_MODE_ECONOMY.md`](../technical/CASH_MODE_ECONOMY.md)
> is the canonical reference for the chip economy as built (pools,
> flow paths, conservation invariant, ledger vocabulary). The
> staking system extends the economy substantially; the economy
> doc is where to verify your understanding of the existing pools
> before adding new ones.

> **What's shipped between this handoff being written and now
> (2026-05-19, late):**
>
> - **Chip ledger v0** (schemas v93+v94). The audit endpoint
>   reports `drift = ledger_outstanding - actual_outstanding`;
>   `drift == 0` is correctness. Use it as your guardrail
>   throughout Phase 1.
> - **Full Sim** (schemas v96+v97) — see
>   [`CASH_MODE_FULL_SIM.md`](../technical/CASH_MODE_FULL_SIM.md)
>   for the technical reference. Real AI hands at unseated
>   tables; psychology persists across sessions and backend
>   restarts; dealer rotates in real engine order. Phase 4's
>   "AI hits forced_leave → take_stake" trigger now has real
>   chip dynamics behind it.
> - **Lobby-seed leak fix** (`f04e048b`). The `BankrollChange`
>   plumbing in `cash_mode/movement.py` is the pattern Phase 4
>   reuses for AI-borrow chip flows. Add a new direction
>   variant rather than inventing new plumbing.
> - **Live drift = 0** is achievable from a clean baseline.

> **Design lock (2026-05-19, evening):** the original handoff
> framed this work as a **loan system** with persistent debt,
> amortization, and multi-loan settlement. A deep design review
> reframed it as a **staking system** — session-based deals,
> light carry, tier-gated offer quality, no payment plans.
> Headline shifts:
>
> | Was (loan model) | Now (stake model) |
> |---|---|
> | Loans persist with outstanding principal | Stakes settle at session end; only **carry** persists |
> | Amortization with periodic minimum payments | No payment schedule — carries sit until cleared by play |
> | Multi-loan stacking (concurrent active loans) | One active stake per session; multiple carries possible |
> | Borrower-side default cascade risk | No cascade — staker exposure bounded at principal |
> | "Tab UI" framing | "Net Worth" view — staker AND borrower position |

This is the v2 layer on top of Path B's sponsorship. The goal is
a **coherent stake economy** where chips, trust, and rivalry all
compound: you owe Napoleon real money, defaulting on Bezos
changes how he plays against you, AIs stake each other when one
busts and the other has bankroll, eventually you stake AIs back,
and the relationship layer carries everything across sessions.

> **Vision in one sentence:** *Staking turns the cash table from
> a sequence of independent sit-down events into a web of
> obligations and shared interests that follows every personality
> across time.*

## The stake model

A **stake** is a deal struck at sit-down, settled at leave-table.
The staker puts up chips; the borrower plays them; at session
end, total chips are split per the agreed cut. If the borrower
busted without recovering the principal, the residual rolls into
a **carry** — a static debt that sits until the borrower works
it down.

Critically: the staker's exposure is **bounded by the principal
they put up**. The chips change hands at deal time. Whatever
the borrower does afterward is bookkeeping — no chain of
obligations propagates back to the staker. This dissolves the
"default cascade" worry that drove some of the original loan-
system caution.

### Three staker kinds

| `staker_kind` | Source | Carry behavior | Used when |
|---|---|---|---|
| `house` | Central bank | Forgives (no carry) | Lender of last resort; always available; baseline-poor terms |
| `personality` | AI bankroll | Creates carry on bust | Most common; relationship-gated |
| `human` | Player bankroll | Creates carry on bust | Phase 5 — player as staker |

### Three offer formats

| Format | Borrower puts up | Staker puts up | Origination fee | Cut |
|---|---|---|---|---|
| **Pure stake** | 0 | full buy-in | yes (from bankroll) | low-mid (15-25%) |
| **Match-and-share** | half buy-in | half buy-in | no | mid-high (40-50%) |
| **House stake** | 0 | full buy-in | small/none | high (40%+) |

Offers are **randomly assembled** at sit-down — a player might
see a match-share offer or might not; willing personalities are
filtered by relationship axes; the house always offers. The tier
system below biases *which* personalities surface, not the
specific terms.

### Carry — light pressure, no amortization

When a borrower leaves with `final_chips < principal`, the
residual becomes `carry_amount`. Carries are:

- **Static.** No hand-count interest, no auto-growth. They sit.
- **Per-staker.** "I owe Napoleon $300, separately owe Bezos $180."
- **Aggregable.** A borrower's `carry_load = Σ carry_amount` across all stakers drives their tier.
- **Capped.** `max_carry = 10 × min_buy_in @ current tier`. Exceed → over-leveraged → house-only.

Two pressure mechanisms apply when a borrower with carry tries
to take a new stake:

1. **Garnishment (per-staker)** — if they had a previous carry with this staker, the new stake's cut is bumped up until the old carry clears.
2. **Tier degradation (aggregate)** — willing personalities drop out as `carry_load` grows; eventually only the house will stake them.

The path back is "grind your way out at bad terms" — house
stakes at high cuts, slowly paying down carry, eventually
re-qualifying for personality offers.

## What Path B shipped

- AI personalities as named stakers (vs. anonymous house archetypes)
- `active_loan_lender_id` on `player_bankroll_state` (schema v90)
- Lender profile per personality (willingness, max-stake-pct,
  rate/floor anchors, relationship gates)
- Offer generation gated by bankroll + likability/heat/respect
- Leave-time settlement credits the AI staker's bankroll
- Three `RelationshipEvent`s: `SPONSORSHIP_OFFERED`, `LOAN_REPAID`,
  `LOAN_DEFAULTED` — names predate the stake rename. Phase 1
  Commit 1 renames them to `STAKE_OFFERED` / `STAKE_REPAID` /
  `STAKE_DEFAULTED` and adds `STAKE_FORGIVEN` for Phase 3's
  forgiveness action.

## What's still missing — the gap this doc fills

1. **No carry across sessions.** Leave the table → loan settles or forgives. Bust → walked away clean. No persistent obligation, so the threat of owing someone is hollow.
2. **Defaults don't enforce.** `STAKE_DEFAULTED` (renamed in Phase 1) fires and shifts respect/heat/likability via dispatch — but no code reads those axes to *refuse* a future stake.
3. **No aggregate debt tracking.** `active_loan_*` columns hold a single loan. Can't represent multi-staker carry positions, which is the v2 unit of debt.
4. **AIs can't be staked.** When an AI busts, they go to the idle pool and wait for bankroll regen. They can't accept a stake from a richer AI (or eventually a player).
5. **No net-worth visibility.** Player has no in-game view of their bankroll position relative to their tier, their carries, or (post-Phase 5) their receivables. All invisible.

This handoff turns those five gaps into a five-phase project.
Each phase ships independently; you can stop after any phase and
have a coherent partial system.

## Phase 1: Session stakes with light carry

### Goal

Replace the three `active_loan_*` columns with a dedicated
`stakes` table. Each row is one session's stake deal. Settlement
at session end converts the row to either `'settled'` (clean) or
`'carry'` (residual debt rolls forward).

### Schema v98 (current as of 2026-05-19 late)

> Schema is at v97. **Phase 1's stakes migration is v98.** Verify
> with `SELECT MAX(version) FROM schema_version` against the
> live DB before starting.

Versions already taken:
- v90: `active_loan_lender_id` on player_bankroll_state (Path B)
- v91: `cash_tables`
- v92: `cash_idle_pool`
- v93: `chip_ledger_entries`
- v94: `pre_ledger_universe` seed
- v95: `relationship_states.notes`
- v96: `cash_tables.dealer_idx`
- v97: `ai_bankroll_state.emotional_state_json`

```sql
CREATE TABLE stakes (
    stake_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,                -- the specific cash session this funds
    staker_id TEXT,                          -- NULL for house; personality_id or owner_id otherwise
    staker_kind TEXT NOT NULL,               -- 'house' | 'personality' | 'human'
    borrower_id TEXT NOT NULL,
    borrower_kind TEXT NOT NULL,             -- 'human' | 'personality'
    format TEXT NOT NULL,                    -- 'pure' | 'match_share' | 'house'
    principal INTEGER NOT NULL,              -- chips the staker put up
    match_amount INTEGER NOT NULL DEFAULT 0, -- chips the borrower put up (match_share only)
    origination_fee INTEGER NOT NULL DEFAULT 0,
    cut REAL NOT NULL,                       -- fraction of net winnings to staker
    status TEXT NOT NULL,                    -- 'active' | 'settled' | 'carry' | 'defaulted'
    carry_amount INTEGER NOT NULL DEFAULT 0, -- residual debt if borrower busted (status='carry')
    stake_tier TEXT NOT NULL,                -- stake_label this deal was made at
    created_at TIMESTAMP NOT NULL,
    settled_at TIMESTAMP
);

CREATE INDEX idx_stakes_borrower_carry ON stakes(borrower_id, borrower_kind, status)
    WHERE status = 'carry';
CREATE INDEX idx_stakes_staker_carry ON stakes(staker_id, status)
    WHERE status = 'carry';
CREATE INDEX idx_stakes_session ON stakes(session_id);
```

The `stake_tier` field is the `STAKES_LADDER` key (`$2`, `$10`,
etc.) the stake was made at. Used by Phase 2 tier resolution and
for analytics on default rates by stake size.

### Phase 1 commit breakdown (~5 commits)

**Commit 1: Vocabulary rename — LOAN_* / SPONSORSHIP_* → STAKE_***
- Pure refactor pass. No new functionality.
- **Relationship event names** in `poker/memory/relationship_events.py`:
  - `LOAN_REPAID` → `STAKE_REPAID`
  - `LOAN_DEFAULTED` → `STAKE_DEFAULTED`
  - `SPONSORSHIP_OFFERED` → `STAKE_OFFERED`
  - Add new `STAKE_FORGIVEN` (Phase 3 surfaces it; wired through the dispatch table now).
- **Ledger reason names** in chip_ledger code + `CASH_MODE_ECONOMY.md`:
  - `house_loan_issue` → `house_stake_issue`
  - `house_loan_settle` → `house_stake_settle`
  - `forgive_balance` stays (it's generic, not loan-specific).
- Touches: `poker/memory/relationship_events.py`, the dispatch table in `opponent_model.py`, axes shift definitions, every `record_event()` caller, `poker/repositories/chip_ledger_repository.py` ledger reason constants, ledger-writer call sites, all tests referencing the old names. Use `git grep -l 'LOAN_REPAID\|LOAN_DEFAULTED\|SPONSORSHIP_OFFERED\|house_loan_'` to find call sites.
- We're still in testing; purge old names entirely, no backwards-compat shim. Existing ledger rows can be migrated in a one-shot UPDATE (`UPDATE chip_ledger_entries SET reason = 'house_stake_issue' WHERE reason = 'house_loan_issue'`) or the table truncated since we're pre-launch.
- Update `CASH_MODE_ECONOMY.md` "Sources" / "Sinks" tables and `CASH_MODE_CHIP_LEDGER_HANDOFF.md` reason vocabulary section to use new reason names.
- Tests: existing relationship-axis tests pass under new event names; ledger writes use new reason names; audit invariant holds; no string references to old names remain (grep check in CI ok).

**Commit 2: Module rename + Schema v98 + repo**
- Rename `cash_mode/stakes.py` (currently holds `STAKES_LADDER`) → `cash_mode/stakes_ladder.py`. Update all imports.
- New `cash_mode/stakes.py` holds the `Stake` dataclass.
- Schema v98: idempotent CREATE TABLE `stakes`.
- New `poker/repositories/stake_repository.py` with
  `create_stake`, `load_stake`, `load_active_for_session`,
  `list_carries_for_borrower`, `list_carries_for_staker`,
  `update_status`, `update_carry_amount`.
- Tests: schema round-trip, status transitions, carry creation.

**Commit 3: Migrate active_loan_* data → stakes rows**
- One-shot migration helper that scans `player_bankroll_state`
  rows with `active_loan_amount > 0`:
  - If the player has an active session: create a stake with `status='active'`, terms transferred verbatim from the columns. Resolve `staker_kind` from `active_loan_lender_id` (NULL → `'house'`, else `'personality'`).
  - If no active session (defensive — shouldn't normally happen post-leave): create `status='carry'` with `carry_amount = active_loan_amount`.
- Don't drop the `active_loan_*` columns yet — Phase 2 finishes the cutover after the new settlement path is live.
- Tests: known-state fixtures migrate correctly; idempotency (re-running doesn't duplicate rows).

**Commit 4: Settlement rewrites against stakes table**
- `cash_mode/loan_settlement.py:settle_loan_on_leave` becomes `cash_mode/stake_settlement.py:settle_stake_on_leave(stake_id, chips_at_leave, repos)`.
- Walks the **single active stake** for the session (not multiple — only one stake per session). Math:
  - `net_winnings = chips_at_leave - stake.principal - stake.match_amount`
  - If `net_winnings >= 0`: staker gets `principal + cut × net_winnings`; borrower gets `match_amount + (1 - cut) × net_winnings`. Mark `status='settled'`.
  - If `net_winnings < 0` AND `chips_at_leave > 0`: staker recovers `min(chips_at_leave, principal)`; borrower gets whatever's left after that (typically 0); `carry_amount = principal - recovered`; mark `status='carry'`.
  - If `chips_at_leave == 0`: staker gets 0; `carry_amount = principal`; `status='carry'`.
- Returns a `StakeSettlement` dataclass with the chip flows for the caller to apply.
- Tests: clean settle at multiple cut ratios; partial-bust carry; full-bust carry; match-share variants.

**Commit 5: House stake forgiveness + chip flow plumbing**
- House stakes never carry. When `staker_kind='house'` and the borrower busted: `status='settled'`, `carry_amount=0`, fire the `forgive_balance` ledger annotation (amount=0) for the unrecovered amount.
- Wire stake creation chip flow via `BankrollChange` (the lobby pattern from `f04e048b`):
  - Personality stakes: staker bankroll → borrower seat, new direction `'staker_to_borrower_seat'`.
  - Human stakes: player bankroll → borrower seat, same direction.
  - House stakes: `house_stake_issue` ledger path (renamed from `house_loan_issue` in Commit 1); chips go central_bank → borrower seat.
- For `match_share`: borrower's own contribution comes from their bankroll (existing `direction='to_seat'`).
- For `origination_fee` (pure stakes): borrower bankroll → staker bankroll at sit-down, separate `BankrollChange`.
- Tests: house stake bust forgives cleanly (audit drift=0); personality stake bust creates carry (audit drift=0); match-share settlement splits per cut; origination fee transfers correctly.

After Phase 1: stakes persist as session records; carries persist as residual debt. Nothing yet *enforces* "you have a carry, can't borrow again from the same staker" — that's Phase 2.

### Phase 1 chip-ledger interaction (do not break the audit)

Phase 1 re-homes the loan state but **must not change which
ledger entries fire from which events**. Mapping under stakes:

| Event | Ledger entry | Fires from |
|---|---|---|
| Player accepts house stake | `house_stake_issue` (central_bank → player seat) for `principal` | `sponsor_and_sit` in `cash_routes.py` |
| Player leaves with house stake, chips ≥ principal | `house_stake_settle` (player seat → central_bank) for principal + cut × winnings | `leave_table` → `settle_stake_on_leave` |
| Player leaves with house stake, chips < principal | `house_stake_settle` for `chips_at_leave` + `forgive_balance` (amount=0) for unrecovered principal | same |
| Personality / human stake flows | **NONE** — pure transfer between two non-bank pools | n/a |
| Match-share / origination-fee flows | **NONE** — pure bankroll-to-bankroll transfers | n/a |

Personality and human stakes move bankroll → bankroll without a
ledger entry; the audit's `actual_outstanding` already counts
both sides, so chip conservation holds without bank involvement.

**Carry creation does NOT fire any new ledger entry.** The chips
were already ledger-accounted at the partial settle. The
`carry_amount` is a tracking field on the stakes row, not a
chip pool. The chips themselves are wherever they ended up via
gameplay — most often in other AIs' seats at the table.

See `CASH_MODE_ECONOMY.md` for why "forgiveness" doesn't destroy
chips (they were redistributed via gameplay, the IOU is what
got written off).

### Phase 1 data-migration scope (do NOT reconcile historical drift)

Phase 1.2's migration is a **shape change only**. If the live
DB carries pre-existing audit drift, don't try to reconcile it
in the migration. That's a separate cleanup tracked in
`CASH_MODE_ECONOMY.md` §"Known issues" item 1.

Don't write synthesizing ledger entries from the migration. The
audit will show the same drift before and after, which is
correct.

## Phase 2: Reputation enforcement + tier-gated offer quality

### Goal

Defaults have teeth. Outstanding carries shift the borrower's
tier and degrade new offers. The relationship axes (already
adjusted by the existing dispatch table) get *read* by the offer
generator. Aggregate carry load adds a system-level brake.

### Phase 2 commit breakdown (~3 commits)

**Commit 1: Tier resolution + per-staker carry-blocking gates**
- New `cash_mode/staking_tier.py:resolve_tier(borrower_id, repos, now) → str` returning one of `'premium' | 'standard' | 'restricted' | 'house_only'`. Driven by:
  - `carry_load = Σ carry_amount across active carries`
  - `max_carry = 10 × min_buy_in @ borrower's current playing tier`
  - Ratio thresholds (configurable; suggested defaults: <20% premium, 20–60% standard, 60–100% restricted, ≥100% house_only)
- `cash_mode/sponsor_offers.py:compute_personality_offers` reads the tier:
  - **Premium**: full list of willing personalities, normal cuts, match-share offered when the personality is in the mood.
  - **Standard**: personalities with low likability/respect drop out; cuts bumped 5-10%.
  - **Restricted**: only forgiving personalities (high likability AND high respect); cuts bumped 15-25%.
  - **House-only**: no personality offers; house only.
- Per-staker garnishment: if the borrower has an existing carry with this specific staker, that staker's offer cut goes up by `garnishment_rate × outstanding_carry_amount / new_principal` (capped at some maximum, suggested +20pp).
- Tests: tier resolution at boundary loads; carry blocks same-staker low-cut offers but allows garnished variant; tier degradation orders willing personalities correctly.

**Commit 2: Explicit default action**
- New POST `/api/cash/stakes/<stake_id>/default` — borrower (player or, via Phase 4, AI) chooses to clear a carry by taking the reputation hit. Marks `status='defaulted'`, emits `STAKE_DEFAULTED` event, zeroes `carry_amount`.
- **No bankroll check** — default is always allowed regardless of whether the borrower could afford to pay. The trade-off the borrower is making is "clear the carry now in exchange for a reputation hit on this lender."
- Different from Phase 1's natural carry behavior (no event, just rolls forward): explicit default fires the reputation hit immediately via the dispatch table.
- The leverage exploit (take cheap stake, win big, default cheap, pocket profit) is tolerated for v1. The carry's tier-degradation pressure is the main consequence anyway; explicit default trades that ongoing pressure for a one-shot reputation hit on one lender. If playtest shows systematic exploitation, follow-up work could scale the reputation magnitude by carry size or by bankroll-vs-carry ratio at default time.
- Tests: default endpoint mutates state; STAKE_DEFAULTED event fires; bankroll unchanged (server doesn't touch it).

**Commit 3: Lobby surfacing of tier + refusals**
- When `compute_personality_offers` excludes a lender for tier or per-staker reasons, capture the rejection ("Napoleon refuses — you defaulted last week").
- Sponsor modal renders the rejected list in a "they won't back you" section so the player sees the cost of past defaults.
- Lobby response carries the current tier so the frontend can render a tier indicator.
- Tests: rejection reasons surface; tier appears in lobby response.

After Phase 2: tiered offer quality + per-staker garnishment make carries painful through the offer surface, without forcing fixed payment schedules.

## Phase 3: Net Worth view

### Goal

Visibility into bankroll position relative to the player's tier,
outstanding carries, and (Phase 5+) receivables. Currently the
bankroll number is misleading because it doesn't reflect carries.

> **Renamed from "Tab UI":** the original framing was loan-centric. Stakes resolve at session end, so the active stake lives in-game; the lobby view's job is the post-session position — net worth, carries, tier — not a running tab.

### Phase 3 commit breakdown (~3 commits)

**Commit 1: Backend `/api/cash/net-worth` route**
- GET → returns:
  - `bankroll`: integer
  - `tier`: stake_label (highest stake the bankroll qualifies for)
  - `tier_status`: `'premium' | 'standard' | 'restricted' | 'house_only'`
  - `carry_cap`: `10 × min_buy_in @ tier`
  - `payables`: list of `{stake_id, staker_id, staker_kind, staker_display_name, carry_amount, created_at}`
  - `receivables`: empty list for now (Phase 5 populates)
  - `net_worth`: bankroll + Σreceivables - Σpayables
  - `available`: carry_cap − Σpayables (how much carry headroom remains)
- `/api/cash/lobby` annotates each `TableCard` where the player has a carry with someone seated at that table.
- Tests: route shape; lobby annotation surfaces correctly.

**Commit 2: Frontend Net Worth drawer**
- New `<NetWorthDrawer>` accessible from `/cash` via an icon next to the bankroll display.
- Lists payables: staker avatar + name, carry amount, age, "Pay off now" action (voluntary — debit bankroll → staker bankroll, mark stake `'settled'`; gated on bankroll covering the carry; greyed out when it doesn't).
- **Receivables column appears in the layout but renders empty in v1** (placeholder waiting for Phase 5). Keep the structural slot so the UI doesn't reshuffle when Phase 5 ships.
- Tier indicator visible: e.g., "$50 stakes — Standard tier".
- TableCard gains a small corner pin on the relevant AI's portrait when the player has a carry with someone seated there.
- Mobile: include in `MobileCashSheet` under a "Net worth" section.
- **Visual budget note:** TableCard already carries emotion-tinted borders (Full Sim psychology), the dealer "D" badge (+ SB/BB), and the activity ticker. Keep the carry indicator small — a corner pin on the AI's portrait, not a banner.

**Commit 3: Forgiveness request action**
- Player taps "Request forgiveness" on a carry in the Net Worth drawer.
- New POST `/api/cash/stakes/<stake_id>/request-forgiveness`:
  - Server reads the staker's relationship state for the borrower (likability, respect, heat from `relationship_states`).
  - Decision logic: weighted threshold — e.g., `(likability × 0.5 + respect × 0.4 - heat × 0.3) > threshold` grants forgiveness. Tunable constant.
  - Granted: clear `carry_amount`, mark stake `'settled'`, fire `STAKE_FORGIVEN` event (positive shift for both sides — the forgiven borrower feels grateful, the staker doesn't lose reputation for being generous).
  - Refused: small likability hit on the borrower's side ("you have some nerve asking"); the carry stays.
- Rate-limit asks: at most one request per stake per 24 hours. Otherwise the player could spam-request until the threshold accidentally crosses.
- UI affordance: button labeled "Request forgiveness" with a small explainer ("Napoleon may forgive this if you've built up enough goodwill.").
- Tests: granted/refused paths fire the right events; rate-limit holds; threshold math respects relationship state.

After Phase 3: the player sees their net worth, sees who they owe, can pay carries voluntarily OR request forgiveness through built-up goodwill, and the lobby surfaces "where my creditors are." Three paths to clear a carry (voluntary pay, request forgiveness, explicit default-with-reputation-hit via Phase 2) without anything forcing bankroll movement.

## Phase 4: AIs as borrowers

### Goal

AIs accept stakes when they bust. Creates an AI-to-AI staking
substrate where the staker's bankroll is genuinely at risk on
the borrower's session outcome. Player observes via the
activity ticker.

### Phase 4 commit breakdown (~5 commits)

**Commit 1: Generalize stake plumbing for AI borrowers**
- The schema from Phase 1 already supports `borrower_kind='personality'` — wire it.
- `cash_mode/staker_profile.py` gains `borrower_profile` (mirror of lender profile but for "do I accept stakes?"). Most AIs default to "yes if I bust"; stoic personalities (Lincoln, Buddha types) `willing=false`.

**Commit 2: AI-borrow movement decision**
- `cash_mode/movement.py:evaluate_ai_movement` gains a `take_stake` decision option, evaluated when the AI would otherwise `forced_leave` (chips ≤ 0.3 × buy_in).
- New helper `find_ai_staker_for(borrower_pid, stake_label, candidates)` picks the best staker from the table's other AIs (or the broader pool, Phase 4.4).
- If a staker qualifies, the AI accepts a stake instead of leaving. Their chips refill to the principal; the stake row is created (`stakes` table, both sides `personality`).
- Tests: AI hitting forced_leave threshold → take_stake when eligible staker exists → stays at table with fresh chips; stoic AI refuses; no eligible staker → falls back to `forced_leave`.

**Commit 3: AI session-end stake settlement**
- When an AI's movement decision triggers leave (any reason: forced_leave, stake_up, take_break, bored_move), settle the active stake via `settle_stake_on_leave` — same path as humans.
- Carry rolls forward if applicable; the AI's `borrower_profile` data tracks their own carry history.
- The AI session in full sim is bounded by sit-down (movement → seat) and leave-table (movement → idle/other-seat). Reuse these events as session boundaries.
- Tests: AI session ends → stake settles → carry created/resolved correctly; AI with carry from previous session takes a new stake → garnishment applies from the new lender.

**Commit 4: Cross-table staker pool**
- Broaden `find_ai_staker_for` from "AIs at this table" to "any AI with capacity in the pool." Richer AIs at other tables can stake busting AIs elsewhere.
- Gated on "AIs at the same OR adjacent stake" so the economy stays localized — a $1000-stake AI doesn't impulsively stake a $2-stake bust.

**Commit 5: AI-stake events in lobby ticker**
- New `EVENT_AI_STAKE` event: "Bezos staked Napoleon for $400 at $10"
- New `EVENT_AI_DEFAULT` event: "Napoleon defaulted on Bezos — carried $400"
- Surfaces the AI economy as visible drama.
- Follow the dedupe pattern in `ActivityTicker.tsx` (`dedupeChipPairs`) — AI stake events come in pairs (staker + borrower POV); show one not both.

### Phase 4 implementation notes

**Reuse `BankrollChange` from `f04e048b`.** AI-borrow chip flow:
- borrower's bankroll → seat (existing `direction='to_seat'`)
- staker's bankroll → borrower's bankroll (new `direction='staker_to_borrower'`)

The pure helpers in `cash_mode/movement.py` keep emitting
`BankrollChange` lists; the caller in `cash_mode/lobby.py`
applies them via `debit_bankroll_for_seat` / `credit_ai_cash_out`.
No new plumbing.

**Close the `ai_seed` ledger gap here.** The economy doc's
"Known issues" §2 flags that new `ai_bankroll_state` rows
aren't ledger-instrumented. Phase 4 will create stake records
for AIs who don't yet have a bankroll row — the implicit save
creates one from nothing. Add an `ai_seed` ledger reason and
fire a `central_bank → ai:<pid>` entry on first write. Closes
a known leak; one ledger reason; ~4 lines.

**There's no AI default cascade.** A common worry is "one AI's
bust triggers another's default." This doesn't happen under the
stake model: when Napoleon stakes Buddha, Napoleon's bankroll
loses the principal *at deal time*. Buddha's subsequent loss
doesn't affect Napoleon's solvency; Napoleon just doesn't get
repaid. The real concern is **slow bankroll deflation** across
many bad-pick stakings — the existing `staker_profile.bankroll_floor`
(2× ai_buy_in) prevents extreme cases. Watch audit telemetry
for AI bankroll medians drifting downward; tune `bankroll_floor`
or `max_outstanding_stakes_per_staker` upward if needed.

**Simultaneous staker AND borrower is a feature, not a bug.** An
AI can be staking other AIs while also being staked themselves —
this is the leverage play we want in the economy. Example: a
$50-tier AI with $3k bankroll takes a stake from Bezos for $8k
to play at $200, AND uses their own $3k to stake an upcoming
$10-tier player. Both ends pay off → big upside. Both ends
fail → the staker AI takes a hit but doesn't propagate it (the
chips Napoleon owes Bezos are still owed regardless of how
Buddha's stake from Napoleon plays out — they're separate
deals). The chip math works because:
- An AI's bankroll already reflects what they've staked out (chips left bankroll at deal time).
- An AI's outstanding carries are tracking fields, not chip claims on the bankroll.
- Therefore `available_to_stake = bankroll` directly. No subtraction needed.

The Phase 4 design lets this emerge naturally. No special-casing required.

After Phase 4: the AI economy mirrors the player economy. AIs
stake each other, default on each other, build histories. The
player's interactions are one node in a larger graph.

## Phase 4.5: AI carry resolution

### Goal

Close the carry-accumulation gap that Phase 4 leaves behind. Phase 4
gives AIs a way to *take* stakes when they bust, but no active
behavior to *resolve* the resulting carries. The three player-side
carry-clearing paths from Phase 3 (voluntary payoff, forgiveness
request, explicit default) plus Phase 2's per-staker garnishment
all have AI-side equivalents that this phase wires.

Without Phase 4.5, AI carries pile up indefinitely as IOUs on the
books. The dossier "they owe" totals grow monotonically and the
system stops feeling like a credit market — it's a one-way
accumulator.

> **What Phase 4.5 is NOT:** new economic mechanisms. Every behavior
> here mirrors a player-side path that already exists (Phase 2's
> garnishment, Phase 3's payoff/forgiveness/default). The only new
> surface is the *triggers* that decide when an AI initiates each
> path — relationship axes, bankroll thresholds, hand-tick rolls.
> The settlement math and stake-row mutations reuse the existing
> repo + chip-flow primitives unchanged.

### Phase 4.5 commit breakdown (~5 commits, modular)

**The first two commits (garnishment + tier gate) are the minimum
viable set.** Together they close the runaway-loop ("an
over-leveraged AI keeps accepting peer stakes") and tie new-stake
terms to repayment history. Commits 3-5 add narrative variety on
top of that base — each one can land independently and the system
stays coherent at any stopping point.

**Commit 1: Garnishment on AI take_stake**
- `find_ai_staker_for` already filters candidates by staker_profile + relationship axes. Add a per-candidate post-filter step: if the candidate has an existing carry from this borrower (queried via `stake_repo.list_carries_for_staker(candidate_id)` filtered to `borrower_id == this borrower`), bump the cut returned by the function.
- Garnishment formula mirrors Phase 2.1: new cut = `min(rate_anchor + garnishment_rate × outstanding_carry / new_principal, MAX_CUT)`. Suggested `garnishment_rate = 0.5`, `MAX_CUT = rate_anchor + 0.20` (cap at +20pp to avoid degenerate 100% cut deals).
- The plumbing already supports a custom cut at stake creation — `StakeCreationChange.cut` is just passed through to the Stake row at lobby application time. No new fields needed.
- Tests: garnishment fires when same-staker carry exists; doesn't fire across different stakers; cut cap holds at extreme carry ratios.

**Commit 2: Tier-gated AI take_stake**
- New gate in `refresh_table_roster`'s take_stake branch: query the borrower's tier via `resolve_tier(borrower_id, borrower_kind=PERSONALITY, current_stake_label=table.stake_label, stake_repo=stake_repo)`. If the result is `'house_only'`, refuse the interception — fall back to `forced_leave`.
- Without this gate, an over-leveraged AI can keep qualifying for peer stakes purely because the lender's relationship-axis filter doesn't read the borrower's tier. The runaway-debt failure mode.
- The lobby already plumbs `stake_repo` through to refresh_table_roster's callbacks; the tier read is one more line in `_borrower_profile_lookup` or a new sibling callback `_borrower_tier_blocked(pid, stake_label) -> bool`.
- House stake fallback is NOT implemented here — house-staker selection is sponsor-flow logic, not movement-time logic. Over-tier AIs simply leave (forced_leave) and re-enter normally next cycle, hoping to bust at a lower-stake table where their tier is house_only's denominator changes the math.
- Tests: AI at premium tier qualifies as before; AI at house_only doesn't intercept take_stake; tier read uses the *target* table's stake label, not the borrower's session stake (matters for cross-table candidates).

**Commit 3: AI-initiated voluntary payoff**
- New `cash_mode/ai_carry_resolution.py:try_voluntary_payoff(personality_id, *, bankroll_repo, stake_repo, relationship_repo, rng, now) → Optional[StakeRepaymentChange]`.
- Trigger: each lobby refresh, for AIs in the idle pool, roll a chance based on `bankroll_factor = bankroll / total_carries`. Floor: `bankroll_factor >= 5` → some chance to pay off the oldest carry. Below 5× total → very low chance. Below 1× → never.
- The repayment chip flow is the same as `POST /api/cash/stakes/<id>/payoff` from Phase 3 commit 1: debit borrower bankroll, credit staker via `credit_ai_cash_out` (same cap-clamp semantics). Fire `STAKE_REPAID`.
- The carry to pay off is picked oldest-first (sorted by `created_at`) — gives the AI economy a "clearing the deck" feel rather than randomly snapping off whichever debt happens to be picked.
- Surface in the lobby ticker as a new `EVENT_AI_PAYOFF` ("Bezos paid off $400 carry to Napoleon"), threshold-gated same as Phase 4.5.
- Tests: bankroll-factor threshold respected; oldest carry picked; staker credited; STAKE_REPAID fires; ticker event emitted above threshold only.

**Commit 4: AI-initiated forgiveness ask**
- Mirror of `POST /api/cash/stakes/<id>/request-forgiveness` (Phase 3 commit 3) but borrower-initiated by an AI. Each lobby refresh, for AIs with carries AND no recent ask (`forgiveness_last_asked` is null or > 7 days ago — tighter than the player route's 24h rate-limit because AI asks are auto-rolled, not user-initiated), roll a chance to ask one of their stakers.
- Trigger probability: scales inversely with the AI's `bankroll_factor` from commit 3 (poor AIs ask more often; flush AIs would rather pay off than ask). Suggested: `ask_prob = 0.05 / max(1, bankroll_factor)` per refresh.
- Decision math is identical to the route: `score = likability × 0.5 + respect × 0.4 − heat × 0.3 > 0.55`. Grant clears the carry + fires `STAKE_FORGIVEN`; refuse fires `STAKE_FORGIVENESS_REFUSED` (both events already calibrated in Phase 3 commit 3's dispatch table).
- Stamp `forgiveness_last_asked` either way so spam clicks (in the human path) and tick-frequency rolls (this path) both respect the same rate-limit.
- Ticker events: `EVENT_AI_FORGIVEN` ("Napoleon forgave Bezos's $400 carry") on grant. Refusal is silent on the ticker — the relationship-axis hit is enough; not every refusal needs to surface.
- Tests: trigger probability respects bankroll_factor; rate-limit respected at the 7-day window; threshold math identical to route; granted path clears carry and stake row.

**Commit 5: AI-initiated explicit default**
- The narrative-strongest action. An AI under sustained pressure (low energy + high carry load + low/zero respect for the lender) cuts the cord — explicitly defaults on one specific carry, accepting the sharp `STAKE_DEFAULTED` reputation hit in exchange for clearing the debt.
- Trigger: per-carry per-refresh roll. Pressure score:
  - +0.4 if `bankroll_factor < 0.5` (drowning in debt)
  - +0.3 if AI's energy < 0.3 (tired/tilted — desperate, willing to burn relationships)
  - +0.2 if staker's `respect_for_borrower < -0.2` (already on bad terms — less to lose)
  - +0.1 if carry is the AI's oldest (long-standing debt easier to walk away from)
- Threshold: `pressure >= 0.6` → some chance to default. Tunable.
- Reuses `POST /api/cash/stakes/<id>/default` settlement shape internally: zero `carry_amount`, flip `status='defaulted'`, fire `STAKE_DEFAULTED` (the sharpest negative event in the dispatch). No chip movement (the default IS the cost).
- Ticker event: `EVENT_AI_DEFAULT` already exists (Phase 4 commit 5). Wire the explicit-default path to emit it too, distinguished from the natural-carry event by a `reason='explicit'` field on the LobbyEvent.
- Tests: pressure threshold respected; correct stake_id zeroed; STAKE_DEFAULTED event fires with sharp negative shifts; ticker reflects the explicit-default reason.

### Phase 4.5 implementation notes

**Where the resolution behaviors run.** All four AI-initiated paths (commits 3, 4, 5 + the implicit garnishment of commit 1) fire from `cash_mode/lobby.py:refresh_unseated_tables`, not from in-session movement. The lobby refresh already iterates every AI in the universe each tick (idle pool + table seats); adding the carry-resolution roll per AI is one more cheap pass per refresh. Seated-session AIs are unaffected — their session's stake settles at leave time via the existing Phase 4 commit 3 path.

**Order matters per AI within a refresh.** When the same AI has multiple carries, the resolution roll picks one carry at a time:
  - Voluntary payoff: oldest-first (clears history)
  - Forgiveness ask: highest-likability staker first (best chance of grant)
  - Explicit default: highest-heat staker first (worst-relationship debts cleared first)

These selection orders give each behavior a distinct "personality" without requiring per-AI configuration — the same Bezos with the same carry portfolio chooses different stakers depending on which mechanism fires.

**No new schema.** Every column needed already exists. `forgiveness_last_asked` (schema v104) covers the forgiveness rate-limit. `stakes.status` already has `'defaulted'` as a value (Phase 1). Garnishment writes to `stakes.cut` at create time (existing column).

**Stopping point matrix.**

| After commit | Coherent state |
|---|---|
| 1 only | Garnishment fires; over-tier AIs still take stakes (gap). Some new-stake terms reflect prior debts. |
| 1 + 2 | **Minimum viable.** Garnishment + tier gate. AI economy can't run away; carries are tied to terms. No active resolution yet — but the system stops drifting toward "everyone owes everyone." |
| 1 + 2 + 3 | Voluntary payoffs reduce the carry book over time. Flush AIs clear their debts naturally. |
| 1 + 2 + 3 + 4 | Forgiveness adds relationship-driven debt clearance. Generous lenders feel different from grudge-holders. |
| All 5 | Explicit defaults add narrative rupture moments. The credit market has high and low points; debts get resolved AND get burned. |

**Relationship axes already encode every gate this phase needs.** No new axes, no new event types beyond what Phase 3 commit 1 + Phase 4 commit 5 already calibrated. The work is entirely in the *triggers* — when each existing path fires for AIs.

**The dossier UI from Phase 4 surfaces all of this for free.** "Owed to them" and "They owe" rows update as carries clear via any of these paths. The ticker events make moment-of-resolution visible. No additional frontend work in Phase 4.5.

After Phase 4.5: the AI credit market is **self-clearing**. Carries get created (Phase 4), get pressured by garnishment + tier (commits 1-2), and get resolved through play (commit 3), goodwill (commit 4), or rupture (commit 5). The cast accumulates financial history rather than accumulating only debt.

## Phase 5: Humans as stakers

### Goal

Once the player has enough bankroll, they can offer stakes to
AIs. This is the **endgame chip-sink mechanism** — wealthy
players deploy capital into the AI population, taking on
bankroll-deflation risk in exchange for a cut of upside.

### Unlock criteria

Player can offer a stake at a given tier iff **bankroll ≥ 1.5 × min_buy_in @ that tier**:

| Stake | Min buy-in | Bankroll needed to stake here |
|---|---|---|
| $2 | $80 | $120 |
| $10 | $400 | $600 |
| $50 | $2,000 | $3,000 |
| $200 | $8,000 | $12,000 |
| $1000 | $40,000 | $60,000 |

This puts staking-at-the-current-tier within reach as soon as
the player is comfortably playing that tier. Tunable post-launch
— move to 2× if exploit potential surfaces in playtest.

### Phase 5 commit breakdown (~3 commits)

**Commit 1: Player-offered stake route + AI evaluation**
- New `POST /api/cash/stakes/offer` — player offers a stake to a specific AI: `{target_pid, principal, cut, match_amount, origination_fee, stake_label}`.
- Server validates: player bankroll ≥ 1.5 × min_buy_in @ stake_label; target AI is staking-eligible (not currently in a session, willing per `borrower_profile`, carry-load within their own tier cap, not in cooldown for prior default to this player).
- AI evaluates using its relationship axes vs the player (likability/heat/respect), its `borrower_profile.willingness_threshold`, and a comparison against other available stake options (the AI shops the best deal in its current pool of offers).
- Response: accepted (stake row created with `staker_kind='human'`, AI sits at the target table) or refused (with reason).
- Tests: bankroll gate; AI accept/refuse logic; stake row created correctly; relationship events fire (`STAKE_OFFERED` — new event type added to dispatch, or reuse `SPONSORSHIP_OFFERED` for v1 backward compat).

**Commit 2: Player-as-staker UI**
- Lobby's AI roster view gains a "Stake this player" action on each AI when the player meets the unlock criteria for at least one tier.
- Action opens a modal with default terms (auto-suggested principal at `min_buy_in @ tier`, default cut from a configurable starting point — say 30%, optional match-and-share toggle, optional origination fee).
- On submit, hits the offer route.
- Net Worth drawer's "Receivables" column populates with active stakes.

**Commit 3: Settlement when the staked AI's session ends**
- When the staked AI's movement decision triggers leave, `settle_stake_on_leave` runs as for any other stake. The player's bankroll is credited with their share.
- New `RelationshipEvent`: `STAKE_REPAID` / `STAKE_DEFAULTED` for AI→player feedback (or reuse `LOAN_REPAID` / `LOAN_DEFAULTED` for v1 backward compat).
- Tests: AI's session ends → player bankroll updates with cut/principal recovery; carry rolls forward when AI busts under player stake; relationship event fires.

After Phase 5: the player is fully participating from both
sides. The endgame chip sinks (hosting tables, appearance fees,
creating custom personalities and staking them) become possible
to layer on top in subsequent phases.

## Locked decisions (2026-05-19) + remaining open questions

1. ~~**Default cooldown duration.**~~ **Locked: 7 wall-clock days.** Phase 2's gate refuses re-staking from any staker the borrower defaulted on within the last 7 days. Configurable constant.

2. **Stake interest / fees.** Origination fee (paid upfront from borrower bankroll) is the primary mechanism. No time-based accrual. The schema admits an `interest_rate_daily` column without redesign if needed later; v1 is flat terms with cut on session-end winnings.

3. ~~**House stakes — persistent or session?**~~ **Locked: session-scoped, forgive on bust.** House never carries. The "lender of last resort" role is preserved; player can take repeated house stakes after busts. Stuck at house-only tier until they grind back via wins.

4. ~~**AI default cascade.**~~ **Locked: not a real risk; simultaneous staker+borrower is a feature.** Staker exposure is bounded at principal — chips leave at deal time, the borrower's subsequent loss doesn't propagate back to the staker. Simultaneous staker+borrower is the intended leverage play (an AI stakes lower-tier players while being staked themselves at a higher tier). Watch AI bankroll medians via audit telemetry; tune `bankroll_floor` and `max_outstanding_stakes_per_staker` if telemetry shows slow deflation.

5. ~~**Player-staking-AI unlocked when?**~~ **Locked: bankroll ≥ 1.5 × min_buy_in @ target tier.** Tunable post-launch.

6. ~~**Reputation across stakes.**~~ **Locked: global per-personality.** Default on Napoleon at $10 → Napoleon refuses at $1000 too.

7. ~~**Match-and-share offer shape.**~~ **Locked: same `stakes` row with `match_amount > 0` and zero origination_fee.** Higher cut than pure stakes (suggested 40-50% vs 15-25%). Offer surfaces randomly — match-share isn't guaranteed in any given offer batch.

8. ~~**Carry cap.**~~ **Locked: 10 × min_buy_in @ borrower's current playing tier.** Drops when the player drops tiers. Over-cap → house-only.

9. ~~**Player-created custom personality as endgame chip sink**~~ **Locked (post-Phase-5 scope).** Player creates a custom personality via the existing personality manager. All player-created personalities are **private to the server instance** — public/private is a server-host decision, not a per-user choice. On creation, the new personality is auto-seeded into the AI pool and behaves like any other AI from there. The creator-borrower relationship starts with a **higher-affinity bonus on top of the standard staking-relationship bond** — the new personality has positive likability toward the creator from day zero (representing the "I created you" affinity), then the relationship evolves naturally from gameplay. The personality counts against the player's bankroll the same as staking any other AI; no special pricing.

10. ~~**House stake economics — chip source semantics.**~~ **Locked: unbounded for v1.** Central bank can always lend; the "lender of last resort" role is preserved. The bank's "reserves" remain a derived ledger value (creations vs destructions vs seed). Revisit if endgame chip sinks (Phase 5+ staking, hosting tables, custom-personality stakes) aren't pulling enough chips back from the player economy.

11. ~~**STAKE_*** vs LOAN_* event names.~~ **Locked: full rename to STAKE_*** across the codebase. `SPONSORSHIP_OFFERED` → `STAKE_OFFERED`. `LOAN_REPAID` → `STAKE_REPAID`. `LOAN_DEFAULTED` → `STAKE_DEFAULTED`. New `STAKE_FORGIVEN` added for Phase 3's forgiveness action. We're still in testing — no backwards-compat shim; purge old names entirely. Phase 1 Commit 1 is the rename pass.

12. ~~**Explicit-default eligibility check.**~~ **Locked: no bankroll gate; default always allowed.** The reputation hit is the cost, not forced bankroll payment. The leverage exploit (take cheap stake, win big, default cheap) is tolerated for v1 — the tier-degradation pressure of a sitting carry is the main consequence anyway, and explicit default trades that ongoing pressure for a one-shot single-lender reputation hit. If playtest shows systematic exploitation, follow-up work could scale the reputation hit by carry size or by bankroll-vs-carry ratio at default time.

13. ~~**Multi-player.**~~ **Locked: out of scope for v1.** The cash-mode design is a single-player sandbox. Phase 5's `staker_kind='human'` ID surface uses `owner_id` and is generalized enough to support a future Minecraft-style private-invite mode, but no multi-player UI, dispute resolution, or human-vs-human relationship surface is in scope.

## Risks to flag before starting

- **Existing players have outstanding loans (the `active_loan_*` fields).** The Phase 1.2 migration handles them by translating into `stakes` rows. Run the migration before the new settlement code path is live, else loans get lost during the cutover.

- **Test combinatorics are manageable here.** Under the original loan model, multi-loan settlement had many branches. Under stakes, there's one active stake per session, so settlement is single-branch: clean settle / partial carry / full bust. The surface shrinks meaningfully.

- **AI-to-AI events could spam the ticker (Phase 4.5).** With full sim running real hands at unseated tables, 4 unseated tables × forced_leave rate × take_stake decisions could fire many events per minute. Apply the same `big_event_threshold` pattern from `cash_mode/full_sim.py` — only surface AI stakes above a chip threshold; smaller ones affect state silently.

- **"Wealthy player owes $5k is a rounding error" problem.** Under stakes, the carry cap is `10 × min_buy_in @ tier` — a $200k player at $1000 stakes has a $200k carry cap. They can owe a lot, but the carry never threatens them mechanically. The structural fix is endgame chip sinks (Phase 5 staking, hosting tables, etc.); reputation enforcement is the narrative consequence but doesn't equalize the economic asymmetry.

- ~~**Cross-stake leverage exploit.**~~ Already documented as **tolerated v1 behavior** in locked decision #12. Mentioned here so the Phase 2 implementer doesn't try to "fix" it by reintroducing a bankroll gate on default — that path was explicitly rejected during design.

## Files to read first

1. **This doc** — design above.
2. **`docs/technical/CASH_MODE_ECONOMY.md`** — canonical chip-economy reference. Pools, flow paths, conservation invariant, ledger vocabulary, known issues. **Read this before touching any chip-moving code.**
3. **`docs/technical/CASH_MODE_FULL_SIM.md`** — sim mechanics that Phase 4 hooks into.
4. **`docs/plans/CASH_MODE_PATH_B_HANDOFF.md`** — what Path B built; defines the staker_profile shape and the relationship event wiring this handoff extends.
5. **`docs/plans/CASH_MODE_CHIP_LEDGER_HANDOFF.md`** — the ledger's reason vocabulary and audit model.
6. **`poker/repositories/schema_manager.py`** — `SCHEMA_VERSION` is 97; Phase 1 lands v98.
7. **`cash_mode/loan_settlement.py:settle_loan_on_leave`** — the function Phase 1.3 rewrites as `cash_mode/stake_settlement.py:settle_stake_on_leave`.
8. **`cash_mode/movement.py:BankrollChange`** — plumbing pattern Phase 4 reuses for AI-borrow chip flows.
9. **`cash_mode/sponsor_offers.py:compute_personality_offers`** — where Phase 2's tier-aware gates land.
10. **`flask_app/routes/cash_routes.py:leave_table`** — current settlement call site; Phase 1.3 updates the contract.
11. **`flask_app/services/chip_ledger_audit.py:compute_audit`** — your correctness probe. Run before + after each commit; `drift` delta should stay 0 except where new ledger entries explain it.
12. **`poker/memory/relationship_events.py`** — `LOAN_*` event names exist here; Phase 1 Commit 1 renames them to `STAKE_*` and adds `STAKE_FORGIVEN` for Phase 3's forgiveness action.
13. **`cash_mode/activity.py`** — Phase 4.5 events + the dedupe pattern (`ActivityTicker.tsx:dedupeChipPairs`) to mirror.
14. **`react/react/src/components/cash/SponsorModal.tsx`** — Phase 2.3 surfaces refusal reasons; Phase 3 adds the Net Worth drawer alongside.

## Suggested ship order

**Phase 1 → Phase 3 → Phase 2 → Phase 4 → Phase 4.5 → Phase 5.**

Reasoning:
- **Phase 1** is load-bearing (everything else assumes the new table). Ship first.
- **Phase 3** (Net Worth view) goes second — gives the player visibility into their carries *before* you add consequences. Avoids "I have a carry I didn't know about and now Napoleon won't talk to me."
- **Phase 2** (enforcement) third — adds teeth after the player can see what they owe.
- **Phase 4** (AI borrowers) fourth — biggest behavior change; needs prior pieces stable.
- **Phase 4.5** (AI carry resolution) immediately after Phase 4 — closes the carry-accumulation gap. Phase 4 ships a one-way accumulator (AIs can borrow but never repay); 4.5 wires the resolution behaviors that make the AI economy self-clearing. Order 4 → 4.5 → 5 keeps the AI side coherent before adding the human-as-staker layer on top.
- **Phase 5** (Human as staker) last — closes the staking loop; unlocks the endgame chip-sink work.

A reasonable v2.5 stopping point is after Phase 3: persistent
carries + Net Worth view + voluntary pay-off is a coherent
shipped product. Phases 2, 4, 4.5, and 5 are real expansions.

A second viable stopping point is **after Phase 4.5 commits 1+2**
(garnishment + tier gate) — the AI economy doesn't *clear* itself
yet, but it stops *runaway accumulation*, which is the failure
mode that breaks playtest pacing. The narrative-variety commits
(4.5 commits 3-5) can land later as polish.

## Why this matters

Each phase amplifies what the previous one set up. Path B made
backing *named* (it's Napoleon, not "Loan Shark"). This system
makes it *consequential* (Napoleon remembers; Napoleon refuses;
Napoleon needs stakes of his own; eventually you stake Napoleon
back). When complete, the cash table isn't a series of
disconnected sessions — it's a persistent financial graph that
the player is a node in. The relationship layer was built for
exactly this; this staking system is what makes it pay off.

## Strategic notes for starting now (2026-05-19 late)

**1. The chip ledger is your safety net.** Run `compute_audit` before and after each commit. If `drift` delta is non-zero on a commit that shouldn't be moving chips (schema migration, data migration, frontend), you have an instrumentation gap. Catch it when the diff is small.

**2. Phase 4 (AI borrowers) is meaningfully more interesting now than when this doc was first written.** Full sim runs real hands at unseated tables; AIs actually lose chips to each other. The "AI hits forced_leave because they ran cold" trigger is no longer hypothetical.

**3. The recommended ship order (1 → 3 → 2 → 4 → 5) holds.** Net Worth before consequences. Reputation before AI borrowers. AI borrowers before human-as-staker (Phase 5 builds on Phase 4's `borrower_kind='personality'` plumbing). Don't skip ahead.

**4. The economy doc (`CASH_MODE_ECONOMY.md`) is the place to update** as backing system phases ship. When Phase 1 lands, add a row to the "Pools" table for `stakes.carry_amount`. When Phase 4 ships, add an "AI stake carries" row. When Phase 5 ships, add "player-staker receivables."

**5. The player-has-no-cap problem gets worse under persistent carries, not better.** A wealthy player can carry meaningful debt as a rounding error. Reputation enforcement (Phase 2) helps narratively but not structurally. The structural fix is endgame chip sinks — Phase 5 staking is the first; hosting tables, appearance fees, custom-personality staking are the rest.

**6. Phase 1 is ~5 commits and you'll be tempted to bundle.** Don't. The event rename (Commit 1), data migration (Commit 3), and settlement rewrite (Commit 4) each have enough surface area that splitting gives the chip ledger a clean diff to verify against. Run `compute_audit` between each commit.

**7. Personality / human stakes are pure transfers; house stakes are ledgered.** Phase 1's `settle_stake_on_leave` dispatch must preserve this distinction. Easy to drop on the floor in a generalization pass — call it out in the test matrix.

**8. v2.5 stopping point after Phase 3 is genuinely viable.** Persistent carries + Net Worth view + voluntary pay-off + forgiveness request is a coherent shipped product on its own. A week of playtest between Phase 3 and Phase 2 would tell you if reputation enforcement feels needed or if visibility + forgiveness texture is enough.

**9. The vocabulary lock matters.** v1 ships a clean `STAKE_*` event vocabulary across the dispatch table, axes shifts, and call sites. Don't reintroduce `loan` terminology in new code. The mental model: a stake is a session deal with a cut on upside, NOT a term loan with amortization. Path B-era variable names will get renamed in the Phase 1 Commit 1 rename pass.

**10. The default cascade you might worry about doesn't exist.** When a staker lends, those chips leave their bankroll immediately. The borrower's subsequent loss doesn't propagate back. The chips are wherever they ended up via gameplay — typically other AIs' seats. Simultaneous staker+borrower is a feature, not a risk — see locked decision #4 and the Phase 4 implementation notes.

**11. Three paths to clear a carry, none of them force bankroll movement.** (1) Voluntary "Pay off now" in the Net Worth drawer — player choice, debits bankroll → staker. (2) Garnishment on a new stake from the same lender — chips come from session winnings, not bankroll. (3) Forgiveness request via relationship axes — no chips move, the IOU is written off. (4) Explicit default — clears the carry in exchange for a reputation hit; no chips move. The deliberate exclusion of "force payment as default precondition" is locked decision #12.

**12. Multi-player isn't on the roadmap.** Phase 5 generalizes `staker_kind='human'` enough that a future private-invite multi-player mode could ship without schema churn, but no v1 or v2 work assumes multi-player. If you find yourself designing for "what if two humans share a session," stop and confirm with the product owner before continuing.
