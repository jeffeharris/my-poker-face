---
purpose: Implementation handoff for the complete loan/backing system — persistent loans across sessions, reputation enforcement, tab UI, and AI-as-borrower. Builds on Path B's session-scoped sponsorship.
type: guide
created: 2026-05-19
last_updated: 2026-05-19
---

# Cash Mode — Backing System Handoff

> **Read first:** [`CASH_MODE_ECONOMY.md`](../technical/CASH_MODE_ECONOMY.md)
> is the canonical reference for the chip economy as built (pools,
> flow paths, conservation invariant, ledger vocabulary). The
> backing system extends the economy substantially; the economy doc
> is where to verify your understanding of the existing pools before
> adding new ones.

> **What's shipped between this handoff being written and now
> (2026-05-19, late):**
>
> - **Chip ledger v0** (schemas v93+v94). The audit endpoint
>   reports `drift = ledger_outstanding - actual_outstanding`;
>   `drift == 0` is correctness. The backing system's loan flows
>   already feed this — Phase 1 must preserve `house_loan_issue` /
>   `house_loan_settle` / `forgive_balance` entries as it
>   re-homes loans into the new table. See "Chip ledger
>   interaction" below.
> - **Full Sim** (schemas v96+v97) — see
>   [`CASH_MODE_FULL_SIM.md`](../technical/CASH_MODE_FULL_SIM.md)
>   for the technical reference. Real AI hands at unseated
>   tables; psychology persists across sessions and backend
>   restarts; dealer rotates in real engine order. Phase 4's "AI
>   hits forced_leave → take_loan" trigger now has real chip
>   dynamics behind it — a busting AI is one who tilted off
>   their stack vs a specific opponent, not a uniform-random
>   chip drift. The relationship layer's heat / respect /
>   likability axes drive lender willingness AND borrower
>   psychology now, which is the texture that made this whole
>   doc worth writing.
> - **Lobby-seed leak fix** (`f04e048b`). New `BankrollChange`
>   plumbing in `cash_mode/movement.py` — pure helper signals
>   what bankroll mutations to apply; caller persists them. **Phase
>   4's AI-borrow chip flow should reuse this exact pattern** —
>   add a `direction='to_seat_loan'` (or similar) variant rather
>   than inventing a new plumbing approach.
> - **Live drift = 0** is now achievable from a clean baseline.
>   Run `compute_audit` before and after each Phase 1 commit;
>   `drift` should not move beyond what your new ledger entries
>   account for.

This is the v2 layer on top of Path B's sponsorship. The goal is a
**coherent loan economy** where chips, trust, and rivalry all
compound: you owe Napoleon real money, defaulting on Bezos changes
how he plays against you, AIs lend to each other when one busts and
the other has bankroll, and the relationship layer carries
everything across sessions.

> **Vision in one sentence:** *Backing turns the cash table from a
> sequence of independent sit-down events into a web of debts and
> obligations that follow every personality across time.*

## What Path B shipped

- AI personalities as named lenders (vs anonymous house archetypes)
- `active_loan_lender_id` on `player_bankroll_state` (schema v90)
- Lender profile per personality (willingness, max-loan-pct,
  rate/floor anchors, relationship gates)
- Offer generation gated by bankroll + likability/heat/respect
- Leave-time settlement credits the AI lender's bankroll
- Three `RelationshipEvent`s: `SPONSORSHIP_OFFERED`, `LOAN_REPAID`,
  `LOAN_DEFAULTED` — all wired through the existing dispatch table

## What's still missing — the gap this doc fills

1. **Loans are session-scoped.** Leave the table → loan settles
   (often forgives a partial balance). You can never "carry debt"
   across sessions, so the threat of owing someone is hollow.
2. **Defaults don't enforce.** `LOAN_DEFAULTED` fires and shifts
   respect/heat/likability via dispatch — but no code reads those
   axes to *refuse* a future loan. Napoleon will gladly lend you
   $500 again tomorrow even if you stiffed him today.
3. **One loan at a time.** `active_loan_*` columns hold a single
   loan. Can't owe Napoleon AND Bezos simultaneously.
4. **AIs can't borrow.** When an AI busts, they go to the idle pool
   and wait for bankroll regen. They can't take a loan from a
   richer AI to keep playing — even though the human can.
5. **No tab visibility.** Player has no in-game view of their
   debts. Past loans, current balance, who they've burned — all
   invisible to the UI.

This handoff turns those five gaps into a four-phase project.
Each phase ships independently; you can stop after any phase and
have a coherent partial system.

## Phase 1: Loans as persistent first-class objects

### Goal

Replace the three `active_loan_*` columns with a dedicated `loans`
table. Loans become rows you can query (history, outstanding
balances, who-owes-whom). One borrower can hold multiple loans
from multiple lenders concurrently.

### Schema v98 (current as of 2026-05-19 late)

> Schema is at v97. **Phase 1's loans-table migration is v98.**
> Use that number when editing `schema_manager.py`. Verify with
> `SELECT MAX(version) FROM schema_version` against the live DB
> before starting — if other work landed between, increment
> accordingly.

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
CREATE TABLE loans (
    loan_id TEXT PRIMARY KEY,
    lender_id TEXT,          -- personality_id; NULL = anonymous house loan
    lender_kind TEXT NOT NULL,   -- 'house' | 'personality'
    borrower_id TEXT NOT NULL,   -- owner_id (player) OR personality_id (AI borrower, Phase 4)
    borrower_kind TEXT NOT NULL, -- 'player' | 'personality'
    principal INTEGER NOT NULL,
    floor REAL NOT NULL,
    rate REAL NOT NULL,
    status TEXT NOT NULL,        -- 'active' | 'settled' | 'defaulted'
    outstanding_principal INTEGER NOT NULL,  -- what's still owed (== principal when active)
    outstanding_floor INTEGER NOT NULL,      -- floor amount still owed
    created_at TIMESTAMP NOT NULL,
    settled_at TIMESTAMP,
    settled_via TEXT,            -- 'full_repay' | 'partial_default' | 'forgiven' | NULL
    last_session_id TEXT         -- last cash session that touched this loan, for audit
);
CREATE INDEX idx_loans_borrower ON loans(borrower_id, borrower_kind, status);
CREATE INDEX idx_loans_lender ON loans(lender_id, status);
```

`outstanding_principal` and `outstanding_floor` decrement as
sessions settle partial repayments — see Phase 1.4 below.

### Phase 1 commit breakdown (~4 commits)

**Commit 1: Schema (next available, was-v93) — loans table + repo**
- Idempotent CREATE TABLE.
- New `poker/repositories/loan_repository.py` with
  `create_loan`, `load_loan`, `list_active_for_borrower`,
  `list_active_for_lender`, `update_loan_status`,
  `update_outstanding`.
- `Loan` dataclass in `cash_mode/loans.py`.
- Tests: schema round-trip, status transitions, defaults.

**Commit 2: Migrate `active_loan_*` data → `loans` rows**
- One-shot migration helper that scans `player_bankroll_state`
  rows with `active_loan_amount > 0` and creates a corresponding
  `loans` row. Mark `status='active'`.
- After migration, the `active_loan_*` columns become legacy /
  cached convenience fields. Reads should prefer `loans` table.
- Don't drop the columns yet — Phase 2 finishes the cutover.

**Commit 3: Settlement reads + writes use the new table**
- `cash_mode/loan_settlement.py:settle_loan_on_leave` becomes
  `settle_all_active_loans(borrower_id, chips_at_table,
  bankroll_repo, loan_repo)`. Walks all active loans, applies
  floor/cut math per loan, builds a settlement plan, persists.
- Settlement order: **lender_id priority** — personality loans
  settle before house loans (a real person waiting > anonymous
  house). Within personality loans, oldest first.
- Pure function returns:
  ```python
  @dataclass
  class MultiLoanSettlement:
      new_bankroll: PlayerBankrollState
      per_loan: List[LoanSettlementResult]  # one per loan touched
      total_to_player: int
  ```
- Tests: single loan (matches current behavior), two loans
  (priority ordering, chip distribution), partial default on one,
  full repay on another.

**Commit 4: Carry-forward as the v2 behavior**
- When `chips_at_table < total_outstanding_floor`, instead of
  forgiving the balance: decrement `outstanding_floor` by what was
  paid, keep `status='active'`. The loan persists into the next
  session.
- New `settled_via='partial_default'` is reserved for explicit
  defaults (Phase 2); plain partial-pay rolls forward as
  `settled_via=NULL`, status stays `active`.
- Add `forgiveness_threshold_chips` config — if a loan's
  outstanding gets below some tiny floor (~1 BB at the loan's
  origin stake), forgive the remainder. Avoids tracking $3
  zombie debts.
- Tests: chips < floor → roll-forward; chips just barely cover
  one of multiple loans → priority order resolves it; tiny
  remainder → forgiven.

After Phase 1: loans persist, multiple loans work, settlement is
generalized. Nothing yet enforces "you defaulted, can't borrow
again" — that's Phase 2.

### Phase 1 chip-ledger interaction (do not break the audit)

The ledger already tracks house-loan flows. Phase 1 re-homes the
loan state into a new table but **must not change which ledger
entries fire from which events**. Preserve:

| Event | Ledger entry | Fires from |
|---|---|---|
| Player accepts house archetype sponsor | `house_loan_issue` (central_bank → player) | `sponsor_and_sit` in `cash_routes.py` |
| Player leaves with active house loan, chips ≥ floor | `house_loan_settle` (player → central_bank) for floor + cut | `leave_table` → `settle_loan_on_leave` |
| Player leaves with active house loan, chips < floor | `house_loan_settle` for paid portion + `forgive_balance` (amount=0 annotation) for the unpaid principal | same |
| Personality lender flows | **NONE** — pure transfer between AI bankroll and player stack | n/a |

After Phase 1.3 (`settle_all_active_loans`) the dispatch logic
walks multiple loans. Each loan's settlement should fire the
matching pair of entries depending on its `lender_kind`. Add an
end-to-end test:
1. `compute_audit` baseline
2. Issue + settle a multi-loan scenario (one house, one personality)
3. `compute_audit` again
4. Assert `drift` delta == 0

Personality-loan principal moves bankroll → bankroll without a
ledger entry; the audit's `actual_outstanding` already counts both
sides (AI bankrolls + player table stack), so chip conservation
holds without bank involvement.

**Carry-forward at session-end (Phase 1.4) does NOT fire any new
ledger entry.** The loan's `outstanding_floor` decrements in the
`loans` table, but no chips moved that weren't already
ledger-accounted in the partial settle. The loan just stays
`status='active'` instead of resolving. Audit drift unchanged.

### Phase 1 data-migration scope (do NOT reconcile historical drift)

Phase 1.2's migration of `active_loan_*` columns → `loans` rows is
a **shape change only**. If the live DB carries pre-existing audit
drift (~1M chips as of 2026-05-19, from the historical lobby-seed
leak that landed before today's fix at `f04e048b`), the loans
migration must not try to reconcile it. That's a separate one-shot
cleanup tracked in `CASH_MODE_ECONOMY.md` §"Known issues" item 1.

Concretely: don't write any synthesizing ledger entries from the
migration. If a `player_bankroll_state` row says `active_loan_amount=500`,
create a `loans` row with `principal=500, outstanding_principal=500`;
don't try to back-fill ledger entries to make the totals add up.
The audit will show the same drift before and after the migration,
which is correct.

## Phase 2: Reputation enforcement

### Goal

Defaults have teeth. Outstanding debt blocks new loans from the
same lender. Heat/respect axes (which the dispatch table already
adjusts) get *read* by the offer generator.

### Phase 2 commit breakdown (~3 commits)

**Commit 1: Lender-side gates in offer generation**
- `cash_mode/sponsor_offers.py:compute_personality_offers` gains
  an explicit gate: if the player has an `active` loan with this
  lender, exclude them.
- Same function: if the player has a `defaulted` loan with this
  lender in the last N days (configurable, default 7 wall-clock
  days), exclude them. This is the "Napoleon won't lend you again"
  enforcement.
- The relationship axis gates (likability/heat/respect) already
  exist from Path B; this commit makes the *outstanding-loan*
  check first so it short-circuits before the axis reads.
- Tests: outstanding loan blocks same-lender offer; old default
  excludes; default older than N days re-enables; multiple
  lenders qualify independently.

**Commit 2: Explicit default action**
- New POST `/api/cash/loans/<loan_id>/default` — player chooses to
  walk away from a loan they can't pay. Marks the loan
  `status='defaulted'`, emits `LOAN_DEFAULTED` event (already
  exists), zeroes outstanding amounts.
- Different from Phase 1's carry-forward: this is *intentional*
  default, surfaces the reputation hit immediately.
- Tests: default endpoint mutates loan state; event fires;
  bankroll unchanged (no chip transfer on default).

**Commit 3: Lobby surfacing of refusals**
- When `compute_personality_offers` excludes a lender for
  reputation reasons, capture the rejection reason in a parallel
  return ("Napoleon refuses — you defaulted last week").
- Sponsor modal renders the rejected lenders in a separate "they
  won't back you" section so the player sees the cost of past
  defaults.
- Tests: rejection reasons surface correctly in the API response.

After Phase 2: the loan economy has consequences. Defaulting is a
real choice with future cost; outstanding tabs prevent
double-dipping.

## Phase 3: Tab UI

### Goal

Visibility into outstanding debt. Currently invisible; the player
has to remember.

### Phase 3 commit breakdown (~2 commits)

**Commit 1: Backend `/api/cash/loans` route**
- GET → returns all `active` loans for the current player,
  enriched with lender display name + relationship hint.
- The lobby's GET `/api/cash/lobby` annotates each `TableCard`
  where the player has an outstanding loan with the table's
  lender. Frontend shows it as a small indicator on the card
  ("owed Napoleon $300").
- Tests: list endpoint shape; lobby annotation surfaces correctly.

**Commit 2: Frontend tab views**
- New `<TabDrawer>` accessible from `/cash` (small button labeled
  "My tabs" or a chip-count indicator next to bankroll).
- Lists active loans: lender avatar + name, principal, outstanding
  floor, sponsor cut rate, history age.
- Per loan: "pay off now" action (if bankroll covers
  outstanding_floor — settles via the existing leave-time math
  but without leaving the table).
- TableCard gains a small badge ("Owe Napoleon $300") when
  player has an active loan with someone seated.
- Mobile: include in the existing MobileCashSheet under a "tabs"
  section.
- **Note on the lobby visual budget:** TableCard now carries
  emotion-tinted card borders (Full Sim psychology persistence),
  the dealer "D" badge (and SB/BB), plus the activity ticker
  below. The "Owe Napoleon $300" indicator should be small and
  visually quiet — a corner pin on Napoleon's portrait, not a
  banner across the card. The card surface is already dense;
  don't add another full-width strip.

After Phase 3: the player can see their tabs, pay them off
voluntarily, and the lobby surfaces "where my creditors are."

## Phase 4: AI as borrowers

### Goal

AIs take loans when they bust. Creates an AI-to-AI economic
substrate that the player observes (and eventually can interact
with — sponsoring an AI, taking sides in their disputes).

### Phase 4 commit breakdown (~5 commits)

**Commit 1: Generalize loan tables for AI borrowers**
- The schema from Phase 1 already supports `borrower_kind='personality'`
  — wire it.
- `cash_mode/lender_profile.py` gains `borrower_profile` (mirror
  of lender profile but for "do I take loans?"). Most AIs would
  default to "yes if I bust," variant: stoic personalities
  (Lincoln, Buddha types) `willing=false`.

**Commit 2: AI-borrow movement decision**
- `cash_mode/movement.py:evaluate_ai_movement` gains a
  `take_loan` decision option, evaluated when the AI would
  otherwise `forced_leave` (chips ≤ 0.3 × buy_in).
- New helper `find_ai_lender_for(borrower_pid, stake_label,
  candidates)` — picks the best lender from the table's other
  AIs (or the broader pool, Phase 4.4).
- If a lender qualifies, the AI takes a loan instead of leaving.
  Their chips refill to a min-buy-in; the loan is persistent
  (same `loans` table, both sides personality).
- Tests: AI hitting forced_leave threshold → take_loan when
  eligible lender exists → stays at table with fresh chips.

**Commit 3: AI-to-AI leave-time settlement**
- When an AI hits forced_leave AND has outstanding loans,
  partial-pay from their final table chips before going idle.
- Same settlement function as the player path
  (`settle_all_active_loans`), now polymorphic on borrower_kind.

**Commit 4: Cross-table lender pool**
- Broaden `find_ai_lender_for` from "AIs at this table" to "any
  AI with capacity in the pool." Models a richer AI offering a
  loan even if they're sitting elsewhere.
- Maybe gated on "AIs at the same OR adjacent stake" so the
  economy stays localized.

**Commit 5: AI-loan events in lobby ticker**
- New `EVENT_AI_LOAN` event type:
  "Bezos backed Napoleon for $400 at $10"
- New `EVENT_AI_DEFAULT` event type:
  "Napoleon defaulted on Bezos — owed $400"
- Surfaces the AI economy as visible drama in the lobby ticker.
- Follow the dedupe pattern already in `ActivityTicker.tsx`
  (`dedupeChipPairs`) — AI loan events come in pairs (lender +
  borrower POV); the ticker should show one or the other but not
  both as separate rows.

### Phase 4 implementation notes

**Reuse the `BankrollChange` pattern from `f04e048b`.** Phase 4's
AI-borrow flow shifts chips between two AI bankrolls + one seat:
- borrower's bankroll → seat (existing pattern, already
  `direction='to_seat'`)
- lender's bankroll → borrower's bankroll (new direction —
  call it `'lender_to_borrower'` or fold into existing pairings)

The pure helpers in `cash_mode/movement.py` should keep emitting
`BankrollChange` lists; the caller in `cash_mode/lobby.py` applies
them via `debit_bankroll_for_seat` / `credit_ai_cash_out`. No new
plumbing needed — extend the dispatch.

**Consider closing the `ai_seed` ledger gap here.** The economy
doc's "Known issues" §2 flags that new `ai_bankroll_state` rows
aren't ledger-instrumented (only the rows that existed at v94
migration time are covered by `pre_ledger_universe`). When Phase
4 creates an AI's first loan record, that AI may not yet have a
bankroll row — the implicit `save_ai_bankroll` call creates one
from nothing. This is a good time to add a new
`ai_seed` reason to `LEDGER_REASONS` and fire a
`central_bank → ai:<pid>` entry when the row is first written.
Closes a known leak; one extra ledger reason; ~4 lines.

After Phase 4: the AI economy mirrors the player economy. AIs lend
to each other, default on each other, build histories. The
player's interactions are one node in a much larger graph.

## Locked decisions (2026-05-19) + remaining open questions

1. ~~**Default cooldown duration.**~~ **Locked: 7 wall-clock
   days.** Phase 2's gate refuses re-lending from any lender the
   player defaulted on within the last 7 days. Configurable
   constant; tune in playtest.

2. **Loan interest accrual.** Still open. Currently loans don't
   grow over time. Phase 1's schema admits an `interest_rate_daily`
   column without redesign, but the v1 calculus is "flat terms,
   settle when you leave." Recommend defer — adding accrual
   changes the player loop significantly. Revisit if playtest
   shows persistent debt is too forgiving (you can owe forever
   with no escalating pressure).

3. ~~**House loans, persistent or session?**~~ **Locked: persistent
   ("follow around"), but not urgent.** House tabs carry across
   sessions, same as personality loans. Phase 1's schema and
   settlement already treat them uniformly; no extra work to
   make house loans persistent. The "not urgent" framing means
   if a sub-commit needs to defer house-loan persistence to ship
   Phase 1 sooner, that's acceptable — auto-settling house loans
   on leave (v1 behavior) is the deferred fallback.

4. ~~**AI bankrupt cascade.**~~ **Locked: sane defaults, tune
   later.** Phase 4's gating uses these starting values
   (constants in `cash_mode/lender_profile.py`):
   - **Lender bankroll floor**: `2 × ai_buy_in` — an AI won't
     lend if their projected bankroll is less than 2× the loan's
     stake (must have real capacity, not bare-cover).
   - **Max outstanding loans per lender**: 2 — one AI can have
     at most 2 active loans receivable. Keeps lending distributed
     across the AI population.
   - **AI-to-AI default cooldown**: 14 days — longer than the
     player cooldown (7) since AIs are conceptually less
     forgiving and the cascade risk is real.
   - **Respect threshold**: reuses existing `lender_profile.respect_floor`
     from Path B (defaults to -0.5 for personality lenders).
     AI lender refuses if their respect for the borrower < this.

   These are starting values. Adjust based on playtest signal
   (specifically: watch the chip ledger audit for AI bankroll
   crashes after Phase 4 ships).

5. **Player can sponsor AIs?** Still open / v3 territory.
   Defer; mention in the doc so we don't paint into a corner.

6. ~~**Reputation across stakes.**~~ **Locked: global
   per-personality.** Default on Napoleon at $10 → Napoleon
   refuses at $1000 too. Reputation is a relationship attribute,
   not a stake-local one.

## Risks to flag before starting

- **Existing players have outstanding loans (the
  `active_loan_*` fields).** The Phase 1.2 migration handles
  them, but the migration script should run before the new
  settlement code path is live, else loans get lost. Standard
  schema-migration ordering applies.

- **Test combinatorics explode.** Multi-loan settlement has many
  branches (priority order, partial pays, carry-forward, all-or-
  nothing default). Write the pure-function tests first
  (Phase 1.3) before wiring the route.

- **UI complexity creep.** TabDrawer + lobby annotations + per-
  loan actions is a lot of UI. v1 can ship with just the list
  view ("here are your tabs") and add the "pay off now" action
  later.

- **AI-to-AI events spam the ticker** (Phase 4.5). 4 unseated
  tables × random AI loans could fire many events per minute.
  Apply the same `big_event_threshold` pattern from fake_sim —
  only surface AI loans above some chip threshold; smaller
  ones just affect state silently.

## Files to read first

1. **This doc** — design above.
2. **`docs/technical/CASH_MODE_ECONOMY.md`** — single canonical
   reference for the chip economy as built. Pools, flow paths,
   conservation invariant, ledger vocabulary, tuning levers,
   known issues. **Read this before touching any chip-moving
   code.**
3. **`docs/technical/CASH_MODE_FULL_SIM.md`** — sim mechanics
   that Phase 4 hooks into (AI hands at unseated tables, real
   chip dynamics, dealer rotation, psychology persistence).
4. **`docs/plans/CASH_MODE_PATH_B_HANDOFF.md`** — what Path B
   built; defines the lender_profile shape and the relationship
   event wiring this handoff extends.
5. **`docs/plans/CASH_MODE_CHIP_LEDGER_HANDOFF.md`** — the
   ledger's reason vocabulary and audit model. Phase 1's loan
   migration must preserve the existing `house_loan_*` entries.
6. **`poker/repositories/schema_manager.py`** — current
   `SCHEMA_VERSION` is 97; Phase 1 lands v98. Mirror the v90
   (loan fields) and v93 (chip_ledger_entries) migration
   patterns.
7. **`cash_mode/loan_settlement.py:settle_loan_on_leave`** — the
   single-loan function Phase 1.3 generalizes.
8. **`cash_mode/movement.py:BankrollChange`** — the plumbing
   pattern Phase 4 reuses for AI-borrow chip flows.
9. **`cash_mode/sponsor_offers.py:compute_personality_offers`** —
   where Phase 2.1's reputation gates land.
10. **`flask_app/routes/cash_routes.py:leave_table`** — current
    single-loan settlement call site; Phase 1.4 changes the contract.
11. **`flask_app/services/chip_ledger_audit.py:compute_audit`** —
    your correctness probe. Run before + after each commit;
    `drift` delta should stay 0.
12. **`poker/memory/relationship_events.py`** — `LOAN_DEFAULTED`
    and friends already exist; Phase 4 adds AI-borrow events.
13. **`cash_mode/activity.py`** — Phase 4.5 events + the dedupe
    pattern (`ActivityTicker.tsx:dedupeChipPairs`) to mirror.
14. **`react/react/src/components/cash/SponsorModal.tsx`** —
    Phase 2.3 surfaces refusal reasons; Phase 3 adds the tab
    drawer alongside.

## Suggested ship order

Phase 1 → Phase 3 → Phase 2 → Phase 4.

Reasoning:
- **Phase 1** is load-bearing (everything else assumes the new
  table). Ship first.
- **Phase 3** (tab UI) goes second — gives the player visibility
  into the new persistent-loan world *before* you add
  consequences. Avoids the experience of "I have a loan I didn't
  know about and now Napoleon won't talk to me."
- **Phase 2** (enforcement) third — adds teeth after the player
  can see what they owe.
- **Phase 4** (AI borrowers) last — biggest change to AI
  behavior; needs all the prior pieces stable so debugging is
  tractable.

A reasonable v2.5 stopping point is after Phase 3: the player
has persistent debt, can see it, can pay it off voluntarily. Phase
2's enforcement and Phase 4's AI economy are the v3 expansion
that turns it from "I owe people" into "I'm part of a money
network."

## Why this matters

Each phase amplifies what the previous one set up. Path B made
sponsorship *named* (it's Napoleon, not "Loan Shark"). This system
makes it *consequential* (Napoleon remembers; Napoleon refuses;
Napoleon needs loans of his own). When complete, the cash table
isn't a series of disconnected sessions — it's a persistent
financial graph that the player is a node in. The relationship
layer was built for exactly this; this backing system is what
makes it pay off.

## Strategic notes for starting now (2026-05-19 late)

A few things have shifted since this doc was written that affect
how I'd actually approach Phase 1:

**1. The chip ledger is your safety net.** Run `compute_audit`
before and after each commit. If `drift` delta is non-zero on a
commit that shouldn't be moving chips (schema migration, data
migration, frontend), you have an instrumentation gap. Catch it
when the diff is small. The audit endpoint is at
`/api/admin/chip-ledger/audit` (admin only) or via
`flask_app.services.chip_ledger_audit.compute_audit()`.

**2. Phase 4 (AI borrowers) is meaningfully more interesting now
than when this doc was written.** Full sim runs real hands at
unseated tables; AIs actually lose chips to each other. The "AI
hits forced_leave because they ran cold" trigger is no longer
hypothetical. By the time you reach Phase 4, the AI population
will have real economic motion to ride on.

**3. The recommended ship order (1 → 3 → 2 → 4) still holds.**
Tab UI before consequences. Reputation before AI borrowers.
Don't be tempted to skip ahead — Phase 2's enforcement gates
read state Phase 1 creates, and Phase 4's AI lending needs
Phase 2's outstanding-debt checks to avoid runaway lending.

**4. The economy doc (`CASH_MODE_ECONOMY.md`) is the place to
update** as backing system phases ship. When Phase 1 lands,
add a row to the "Pools" table for active loan principals from
the new `loans` table (replacing the `active_loan_*` field).
When Phase 4 ships, add an "AI loans" pool and the new
`EVENT_AI_LOAN` ledger reason (if you choose to ledger AI loans).

**5. The player-has-no-cap problem (economy doc §"Known issues"
item 3) gets *worse* under persistent loans, not better.** A
player with $200k bankroll and Phase 1 persistent debt can owe
$5k to Napoleon but it's a rounding error against their pile.
Reputation enforcement (Phase 2) helps a bit (Napoleon refusing
matters for narrative even if not for chips), but the structural
fix is the unwritten chip-sink work. Note in your playtest
journal if/when persistent debt starts feeling toothless against
a high-bankroll player.

**6. Phase 1 is ~4 commits and you'll be tempted to bundle.**
Don't. The data migration (Commit 2) and the settlement
generalization (Commit 3) each have enough surface area that
splitting them gives the chip ledger a clean diff to verify
against. If you bundle and drift moves, you'll be bisecting which
half caused it.

**7. Personality loans are pure transfers; house loans are
ledgered.** Phase 1's `settle_all_active_loans` dispatch must
preserve this. The settle function needs the loan's `lender_kind`
on every loan to decide whether to fire `house_loan_settle` or
emit no ledger entry. Easy to drop on the floor in a generalization
pass — call it out in the test matrix.

**8. v2.5 stopping point after Phase 3 is genuinely viable.**
Persistent debt + visible tabs + voluntary pay-off is a coherent
shipped product. Phases 2 and 4 are real expansions; don't feel
compelled to chain straight through. A week of playtest between
Phase 3 and Phase 2 would tell you if reputation feels needed
or if persistent debt alone is enough texture.
