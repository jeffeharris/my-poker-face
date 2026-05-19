---
purpose: Implementation handoff for the complete loan/backing system — persistent loans across sessions, reputation enforcement, tab UI, and AI-as-borrower. Builds on Path B's session-scoped sponsorship.
type: guide
created: 2026-05-19
last_updated: 2026-05-19
---

# Cash Mode — Backing System Handoff

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

### Schema v93

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

**Commit 1: Schema v93 — loans table + repo**
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

After Phase 4: the AI economy mirrors the player economy. AIs lend
to each other, default on each other, build histories. The
player's interactions are one node in a much larger graph.

## Open design questions

1. **Default cooldown duration.** Phase 2 says "default blocks
   re-lending for N days, default=7." That's wall-clock days.
   Pin or playtest? 7 may be too long for a daily-play game; 1
   may be too short to feel like a consequence.

2. **Loan interest accrual.** Currently loans don't grow over
   time. Should sitting on debt for a week cost extra? Phase 1's
   schema admits an `interest_rate_daily` column without
   redesign, but the v1 calculus is "flat terms, settle when you
   leave." Adding accrual changes the player loop significantly
   — might be too much.

3. **House loans, persistent or session?** Phase 1 makes loans
   persistent across the board, including house loans (anonymous
   archetype). Is that the right call, or should house loans
   keep the v1 session-scoped behavior (they auto-settle) and
   only personality loans become persistent? Argument for split:
   the personality loans carry the emotional weight; house
   loans are just a cash floor.

4. **AI bankrupt cascade.** If Napoleon defaults on Bezos, Bezos
   loses $300. If Bezos was already low-bankroll, this might tip
   *him* into forced_leave on his next session. Then Bezos takes
   a loan, defaults, cascades to another AI... the AI economy
   could collapse to chaos under heavy AI lending. Phase 4
   needs gating: only AIs above some bankroll floor can lend,
   only AIs in some respect band can be borrowed-from.

5. **Player can sponsor AIs?** v3 territory but worth flagging.
   Could the player back Napoleon when he busts? Creates a third
   role (player as backer) and a new gameplay loop ("invest in
   personalities, earn passive returns from their winnings").
   Defer; mention in the doc so we don't paint into a corner.

6. **Reputation across stakes.** If you default on Napoleon at
   the $10 table, does he refuse to lend at $1000 too? My
   instinct: yes, refusal is per-personality not per-stake.
   Reputation is global.

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
2. **`docs/plans/CASH_MODE_SPONSORSHIP_HANDOFF.md`** §"v2
   deferred" — the original sketch.
3. **`docs/plans/CASH_MODE_PATH_B_HANDOFF.md`** — what's already
   built; defines the lender_profile shape and the relationship
   event wiring.
4. **`poker/repositories/schema_manager.py`** — v90 (loan fields)
   and v92 (cash_idle_pool) are the patterns to mirror for v93
   (loans table).
5. **`cash_mode/loan_settlement.py:settle_loan_on_leave`** — the
   single-loan function Phase 1.3 generalizes.
6. **`cash_mode/sponsor_offers.py:compute_personality_offers`** —
   where Phase 2.1's reputation gates land.
7. **`flask_app/routes/cash_routes.py:leave_table`** — current
   single-loan settlement call site; Phase 1.4 changes the contract.
8. **`poker/memory/relationship_events.py`** (or wherever the
   `RelationshipEvent` enum lives) — `LOAN_DEFAULTED` and
   friends already exist; Phase 4 adds AI-borrow events.
9. **`cash_mode/activity.py`** — Phase 4.5 events land here.
10. **`react/react/src/components/cash/SponsorModal.tsx`** —
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
