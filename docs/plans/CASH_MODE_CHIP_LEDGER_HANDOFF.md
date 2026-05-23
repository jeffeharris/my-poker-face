---
purpose: Implementation handoff for the chip ledger — observability layer that tracks every chip creation/destruction event without changing game behavior. Foundation for a closed-economy "central bank" later if the data shows it's needed.
type: guide
created: 2026-05-19
last_updated: 2026-05-19
implementation_status: 6 commits shipped (schema v93 ledger + repo + creation/destruction instrumentation + audit endpoint + admin panel + v94 pre_ledger_universe seed)
---

# Cash Mode — Chip Ledger Handoff (v0: observability only)

## Why

The current chip economy is **open** — chips appear from nowhere
(AI regen, player seed, house sponsor loans) and disappear into
nowhere (AI bankroll cap clamps, forgiven loan balances). Over
time the net flow drifts. We don't know in which direction or
at what rate, because nothing records the flow.

This handoff ships a **ledger-only** observability layer. It does
not change any game behavior. It does not enforce conservation.
It just writes one row per chip-creating or chip-destroying event
to a new table, and exposes an audit query that reports the
running totals.

> **Decision logic:** ship this, run for a week, look at the
> data. Maybe AI regen rates are wildly inflationary and tuning
> them fixes 80% of the problem. Maybe the cap clamp already
> pulls enough back. Until we have numbers, "central bank" is
> solving an imagined problem.

If the numbers say enforcement is needed, the **central bank v1**
handoff (not yet written) follows — same ledger, but with a
`reserves` value and pause-on-empty semantics.

## What the ledger is

One append-only table. One row per chip event. Each row records:

- `source` — where the chips came from (`central_bank`, a player,
  an AI, or a table)
- `sink` — where they went (same vocabulary)
- `amount` — positive integer
- `reason` — categorization (`player_seed`, `ai_regen`,
  `cap_clamp`, `house_stake_issue`, `house_stake_settle`,
  `forgive_balance`)
- `created_at`, `context_json`

`central_bank` is the abstract source/sink for chip creation and
destruction. When the central bank is on either side of a
transfer, chips entered or left the universe. When both sides
are non-bank, it's a pure transfer (the entities just exchanged
chips).

**v0 scope: only record the central_bank ↔ X transfers.** Pure
transfers between non-bank entities (sit-down debit, leave
credit, fake-sim chip movement, in-game pots) are NOT tracked.
We're answering "is the economy inflating?" — the internal
moves don't affect that.

## Schema v93

```sql
CREATE TABLE chip_ledger_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- 'central_bank' | 'player:<owner_id>' | 'ai:<personality_id>'
    source TEXT NOT NULL,
    sink TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),

    -- One of the categorization strings listed below. New reasons
    -- can be added; old data isn't migrated.
    reason TEXT NOT NULL,

    -- Optional structured context. JSON. Examples:
    --   {"game_id": "cash-...", "loan_lender": "napoleon"}
    --   {"projected_chips": 1500, "cap": 10000}
    context_json TEXT
);

CREATE INDEX idx_chip_ledger_created ON chip_ledger_entries(created_at DESC);
CREATE INDEX idx_chip_ledger_reason ON chip_ledger_entries(reason);
```

### Reason vocabulary

| Reason | Direction | Fires when |
|---|---|---|
| `player_seed` | `central_bank → player:<owner_id>` | First-time entry into cash mode (`_load_or_seed_player_bankroll` writes the seed row). |
| `ai_regen` | `central_bank → ai:<pid>` | Any AI bankroll write where the projected value exceeds the stored value. The diff is the regen amount. |
| `cap_clamp` | `ai:<pid> → central_bank` | Any AI bankroll write where the to-be-stored value would exceed `bankroll_cap`. The overflow goes to the bank. |
| `house_stake_issue` | `central_bank → player:<owner_id>` | Borrower accepts a house-archetype stake offer. Principal amount is created. |
| `house_stake_settle` | `player:<owner_id> → central_bank` | Leave-time settlement of a house stake: floor + cut amounts return to bank. (Personality and human stakes are pure borrower↔staker transfers; not ledger entries.) |
| `forgive_balance` | (no transfer — annotation only) | When a borrower leaves with chips < stake floor and the balance is forgiven. The forgiven amount **never existed** in the borrower's bankroll, but the stake creation entry from earlier (`house_stake_issue`) recorded it. The forgiveness annotation lets us reconcile: total `house_stake_issue` minus total `house_stake_settle` should equal outstanding house-stake principal. If it doesn't, there's a leak. Reason exists for audit clarity, no actual chip movement. |

For v0 we **don't** track personality-loan creations as ledger
entries — those are pure transfers between two non-bank
entities (lender's bankroll → player's stack → lender's bankroll
on settle). No chips entered or left the universe.

## Audit endpoint

`GET /api/admin/chip-ledger/audit`:

```jsonc
{
  "ledger_totals": {
    "chips_created": 124300,       // sum of central_bank → X amounts
    "chips_destroyed": 12100,      // sum of X → central_bank amounts
    "outstanding": 112200          // created - destroyed
  },
  "actual_totals": {
    "player_bankrolls": 1240,
    "ai_bankrolls_projected": 109800,
    "cash_table_seats_ai": 800,
    "active_loans_principal": 360,
    "actual_outstanding": 112200
  },
  "drift": 0,                       // ledger.outstanding - actual.outstanding
  "by_reason": {
    "ai_regen": 102000,
    "player_seed": 200,
    "house_stake_issue": 22100,
    "cap_clamp": -8400,
    "house_stake_settle": -3700,
    "forgive_balance": 0            // annotation only; doesn't affect totals
  },
  "by_reason_window_24h": { ... }   // last 24h
}
```

`drift = 0` means the ledger and the actual chip locations agree
— no leak. Non-zero drift means somewhere chips moved without a
ledger entry, and we need to find the bypass.

**v0 explicitly does NOT enforce that drift stays at 0.** It just
reports. A weekly drift check via cron / log search would tell us
if leaks are accumulating.

## Phase / commit breakdown (~5 commits)

**Commit 1: Schema v93 + repo + ledger module**
- Migrate `chip_ledger_entries` table.
- `poker/repositories/chip_ledger_repository.py` with
  `record(source, sink, amount, reason, context_json)`,
  `sum_creations_by_reason()`, `sum_destructions_by_reason()`,
  `recent_entries(limit)`.
- `core/economy/ledger.py` thin wrapper that's the canonical
  call surface for instrumentation (so future enforcement can
  replace the implementation without rewriting call sites).
- Tests: schema round-trip, record + sum.

**Commit 2: Instrument creation events**
- `_load_or_seed_player_bankroll` writes a `player_seed` entry
  when a new player_bankroll_state row is created.
- AI bankroll write path (find every `bankroll_repo.save_ai_bankroll`
  call): compute regen amount = `new.chips - (old?.chips or new.chips)`,
  write `ai_regen` if positive.
- House sponsor route writes `house_stake_issue` when a house-archetype
  stake offer is accepted.
- Tests: each call site fires the expected ledger entry; amounts
  match.

**Commit 3: Instrument destruction events**
- AI bankroll write path: compute overflow = `pre_clamp - cap`,
  write `cap_clamp` if positive. Note: this needs the pre-clamp
  value to be available; tweak `credit_ai_cash_out` to return
  it, or compute inline.
- Leave-time settlement: detect house-stake settle path
  (`bankroll.active_loan_lender_id IS NULL` AND stake was active),
  write `house_stake_settle` for the sponsor_total.
- Forgive-balance annotation: when `chips_at_table < floor` AND
  it was a house stake, write a `forgive_balance` entry with
  `amount = 0` (annotation only) and context recording the
  forgiven principal — purely for audit reconciliation.

**Commit 4: Audit endpoint**
- `GET /api/admin/chip-ledger/audit` route, admin-only.
- Computes ledger totals + actual totals + drift.
- Returns the JSON shape above.
- Tests: seeded ledger + bankroll/table data → expected totals;
  drift is 0 when state is consistent; non-zero drift when an
  out-of-band chip mutation is simulated.

**Commit 5: Admin UI panel + docs**
- Add a "Chip economy" section to the admin dashboard with the
  audit numbers (total created/destroyed, drift, top reasons,
  24h windows).
- Brief docs sweep cross-linking to backing system + full sim
  handoffs (they'll both want this data when enforcement comes
  up).

## What v0 deliberately doesn't do

- **No enforcement.** Regen runs at full rate. Loans issue
  freely. Cap clamps still evaporate chips. The point is data
  collection, not gameplay change.
- **No reserves.** No `central_bank.reserves` value yet. The
  ledger is append-only; the "bank" is a conceptual source/sink.
- **No tracking of personality-loan creation.** Path B loans are
  pure transfers (AI bankroll → player stack → AI bankroll on
  settle). Tracking them would add many entries with no signal
  for the inflation question.
- **No tracking of internal table moves.** Fake-sim chip swaps,
  pots paid out during live hands, sit-down debits — all
  internal transfers that don't move chips in/out of the
  universe.
- **No player-facing surface.** Admin-only for v0. If we ever
  make the bank a gameplay element ("the house is up $4k"),
  that's its own design pass.

## What to do with the data

After ~1 week of normal usage, the audit endpoint tells us:

- **Net inflation rate.** Created minus destroyed per day. If
  it's growing fast, AI regen rates are too generous.
- **Per-reason breakdown.** Which sources dominate. Probably
  `ai_regen` × 53 personalities × avg rate.
- **Drift.** Should be 0 if instrumentation is complete. Non-
  zero drift identifies the bypass paths.

Use that data to:

1. **Tune AI regen rates** (lowest-hanging fruit).
2. **Decide whether enforcement is needed** at all. Maybe the
   numbers are fine.
3. **Spec the central bank v1 handoff** (if needed) with real
   numbers for reserves seed value and pause-threshold.

## Open questions for v0

1. **`ai_regen` accounting moment.** Today, `project_bankroll`
   returns a higher value than stored; that value only gets
   *persisted* when a chip-moving event writes it back. Should
   the ledger entry fire at projection (read) or at write?
   **Recommendation:** at write. Read-time projection is virtual;
   the write commits it to the universe.

2. **Pre-existing chips.** When the ledger ships, the DB already
   has chips in player + AI bankrolls. The ledger starts empty.
   `chips_created - chips_destroyed` will be 0 but
   `actual_outstanding` will be the pre-existing total → big
   drift from day 1. Two options:
   - Seed the ledger at migration time with one big
     `central_bank → universe` entry equal to the current total.
     Drift starts at 0.
   - Document the pre-ledger total as a constant offset;
     `drift = ledger_outstanding - (actual - pre_ledger_total)`.
   - **Recommendation:** seed at migration with reason
     `pre_ledger_universe`. Clean math; no constants to remember.

3. **Idle pool AIs.** Their chips live in `ai_bankroll_state` —
   counted in `ai_bankrolls_projected`. No special handling
   needed.

4. **Active session AI table stacks.** When a player is at a
   table, the AI chips live in `state_machine.game_state.players
   .stack`, NOT in any persisted location. They came from
   `ai_bankroll_state` via sit-down debit. Need to either count
   them in `actual_outstanding` (requires reading live game
   state) or exclude both sides (debit the bankroll only on
   leave). Today the sit-down path already debits at sit-time,
   so the bankroll reflects the *debited* state; we need to
   add the live table stack to `actual_outstanding`.
   **Recommendation:** the audit sums live `Player.stack` from
   game_state_service for active cash sessions and adds them in.

5. **Loan-principal accounting.** Active house loans have chips
   "out in the world" via the player's stack. Active personality
   loans don't (they're pure transfers — already counted via
   the AI's debited bankroll + player's table stack). For house
   loans, the principal IS in the player's table stack, so as
   long as we count live table stacks, we don't double-count.
   Need a test that confirms this.

## Risks

- **Instrumentation completeness.** Every chip in/out event has
  to fire a ledger entry. Missing one = silent drift. Add a
  test that simulates a session end-to-end and asserts drift
  stays at 0.
- **Performance.** One INSERT per chip event isn't free; expect
  a few hundred entries per active session. Index on `reason`
  + `created_at` keeps audit queries fast. If this grows beyond
  a million rows in a year, add periodic rollup to a
  `chip_ledger_daily_summary` table.
- **Schema drift on `reason` strings.** Free-form strings rather
  than an enum. Pro: extensible. Con: typos. Mitigation: a
  module-level `LEDGER_REASONS` set that all helpers reference;
  reject writes with unknown reasons (or warn-log them).

## Files to read first

1. **This doc.**
2. **`poker/repositories/schema_manager.py`** — v90, v91, v92
   migration patterns mirrored for v93 (already shipped in commit 1).
3. **`cash_mode/bankroll.py:credit_ai_cash_out`** — the main AI
   bankroll write path; needs instrumentation in commits 2+3.
4. **`flask_app/routes/cash_routes.py:_load_or_seed_player_bankroll`** —
   player seed entry point.
5. **`flask_app/routes/cash_routes.py:sponsor_and_sit`** —
   sponsor loan issuance.
6. **`cash_mode/loan_settlement.py:settle_loan_on_leave`** —
   house loan settle path.
7. **`docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md`** — parallel
   work; the ledger is a peer that will inform their tuning.

## What this unlocks

After ship + a week of data:

- **Tuning.** AI regen rates probably need adjusting; the data
  tells us by how much.
- **Central bank v1 handoff.** With real numbers, we can spec
  the enforcement layer correctly (reserves seed value,
  pause-empty thresholds, what happens at empty).
- **Player-visible economy** (v2 / v3). "The house is up $4,200
  this week" as a meta-game element. Only sensible after the
  numbers stabilize.

The ledger is the foundation for any economic enforcement that
ships later. Even if we never enforce, the instrumentation
catches silent leaks early — every new chip-moving code path
needs to declare its source/sink, which forces the design
question "where did these chips come from?" to be answered at
review time.
