---
purpose: Implementation handoff for cash-mode sponsorship — session-scoped loans, rebuy modal, varying terms, AI-opponent sponsors deferred to v2
type: guide
created: 2026-05-18
last_updated: 2026-05-18
---

# Cash Mode — Sponsorship + Rebuy Flow Handoff

This doc gets a fresh context up to speed on the next chunk of work
for cash mode: the player-bust rebuy modal and the session-scoped
sponsorship-loan mechanic. The canonical cash spec
(`CASH_MODE_AND_RELATIONSHIPS.md` Part 2) talks about player bust
semantics; this is the v2 refinement of that mechanic after live
playtest revealed the auto-$5k fresh-grant was too generous and
flavorless.

The deeper design intent: **debt is a stakes amplifier**, and
sponsorship is the seed of a sandbox where AI opponents can later
become lenders themselves. v1 builds the anonymous-house version;
v2 lets specific AI personalities sponsor you.

## Current state of cash mode

**Cash mode v1 is shipped on `phase-1` branch** as a flavor of
the tournament game flow (commits in range
`613c0e9b`..`08b50900`). It is **playable end-to-end in the
browser** — player picks stake, sits at the table, plays hands
against the existing AI controllers through the existing
tournament UI (cards, pot, animations, action buttons, chat,
psychology panels — all reused).

What's already in place:
- Schema v88: `ai_bankroll_state`, `player_bankroll_state`,
  bankroll knobs in `personalities.config_json.bankroll_knobs`
- `BankrollRepository` with `load_*` / `save_*` / `load_personality_knobs`
- `cash_mode/bankroll.py` dataclasses + `project_bankroll`
- `cash_mode/seating.py` pure accounting transitions
- `PersonalityRepository.list_eligible_for_cash_mode`
- `/api/cash/start` — creates a tournament-shape game with
  `cash_mode=True` flag, no `tournament_tracker`. Debits player + AI
  bankrolls, registers in `game_state_service`. Returns `game_id`.
- `/api/cash/leave` — returns chips to bankroll, kills the game.
- `/api/cash/topup` — between-hands chip add from bankroll.
- `/api/cash/state` — refresh-recovery snapshot.
- `_refill_cash_seats` in `flask_app/handlers/game_handler.py` —
  fires between hands when a non-human seat hits stack=0, swaps in
  a fresh personality from the eligible pool, debits its bankroll,
  rebuilds the controller.
- Frontend: cash-entry page at `/cash` with stake picker. Sit-down
  navigates to `/game/<game_id>` which is the existing tournament
  UI. Back arrow in the game header detects `cash-` prefix and
  calls `/api/cash/leave` before navigating away.
- Cash games are filtered out of `/api/games` (continue list) via
  the `cash-` game_id prefix.

**Tournament-bust assumptions are bypassed** because cash games
don't have a `tournament_tracker` — `handle_eliminations` and
`check_tournament_complete` early-return when no tracker exists
(this is a quirk of the existing code that the cash flow uses for
free).

## What's missing — the gap this doc fills

**Player rebuy is unhandled.** When the human's `stack` hits 0,
the seat stays seated with 0 chips. Subsequent hands auto-fold
them (they can't post blinds). The current `/api/cash/leave`
returns 0 chips and a `full_bankroll_bust` (auto-$5k fresh-grant)
fires only when both bankroll AND table stack are 0.

**The auto-$5k grant has been retired by user decision.** Replaced
with the sponsorship-loan flow described below.

## Design — sponsorship + rebuy

### Bust modal (frontend)

Fires when `player.is_human` AND `player.stack == 0` AND
`hand_in_progress == False`. The modal options depend on the
player's bankroll state vs the table's `min_buy_in`:

| Bankroll state | Modal options |
|---|---|
| Bankroll ≥ table's min_buy_in | **Rebuy $X** (table min) / **Top up to max ($Y)** / **Leave table** |
| 0 < bankroll < table's min_buy_in | "You can't afford to rebuy at this table." **Leave table** (drops them at cash entry where they can pick a lower stake) |
| Bankroll == 0 | "You're out of chips. Take a sponsorship offer:" — three offers (see below) / **Quit to menu** |

**Minimum-buy-in rule**: rebuys must be ≥ table's `min_buy_in`
(40 BB). No short-stacking. Same rule applies to initial sit-down
in the cash-entry page — the stake picker should hide stakes
where bankroll < min_buy_in.

### Sponsor offers

When bankroll == 0, the modal shows three anonymous "house"
sponsorship offers. Pick one; chips arrive in bankroll, loan is
recorded.

| Offer | Loan amount | Cut of winnings |
|---|---|---|
| **Friendly** | $200 | 20% |
| **Standard** | $500 | 35% |
| **Loan shark** | $1000 | 50% |

Numbers are tunable — they live in a module-level dict in the
cash route. Bigger loan = bigger cut, so the player makes a real
choice: take small now and stay flexible, or take big and accept
heavier dues.

### Leave-time math (the load-bearing part)

At `/api/cash/leave`, when an active loan exists:

```
chips_at_table   = current Player.stack (the take)
loan_amount      = active_loan_amount from player_bankroll_state
loan_rate        = active_loan_rate

# Step 1: pay back the loan in full from the take.
loan_repaid    = min(loan_amount, chips_at_table)
remaining      = chips_at_table - loan_repaid

# Step 2: of what's left ("winnings"), the sponsor's cut.
sponsor_cut    = int(remaining * loan_rate)
returning      = remaining - sponsor_cut

# Step 3: persist
bankroll.chips += returning
bankroll.active_loan_amount = 0
bankroll.active_loan_rate   = 0.0
```

Edge case — **partial repayment**: if `chips_at_table < loan_amount`,
the entire stack goes to the sponsor, the remainder of the loan is
**forgiven** for v1. (Future iterations can add reputation /
can't-borrow-from-this-sponsor-again penalties.)

Edge case — **no winnings**: if `remaining == 0` after loan
repayment, no cut is taken. Player gets $0 back to bankroll, loan
is cleared.

### Schema v89

Add to `player_bankroll_state`:
- `active_loan_amount INTEGER NOT NULL DEFAULT 0`
- `active_loan_rate REAL NOT NULL DEFAULT 0.0`

Both reset to 0 when player leaves a table. Loans don't persist
across sessions in v1.

The migration's body is just `ALTER TABLE` for the two columns.
Existing `BankrollRepository` `load_player_bankroll` /
`save_player_bankroll` need to be extended to read/write the new
fields.

`PlayerBankrollState` dataclass in `cash_mode/bankroll.py` gets
two new fields (both default 0/0.0).

### v2 deferred (don't build yet)

- **AI-opponent sponsorship.** "Napoleon will lend you $500 at 35%."
  Affects the relationship layer: now you owe a specific AI, future
  hands carry stakes, AI behavior shifts toward the borrower (more
  aggressive when sponsoring? less likely to bluff out of solidarity?).
  Each personality could have a sponsorship profile (does this AI
  lend? at what terms? affected by their `heat`/`respect` toward
  the player). This is the **real** sandbox payoff.
- **Reputation / credit damage** for forgiven balances.
- **Persistent loans across sessions** — if you leave the table
  with unpaid debt, it carries to your next sit-down. v1 says
  session-scoped only to dodge the persistence + UI surface.
- **Multiple concurrent loans** from different sponsors.
- **Stake-entry filter** that hides stakes the player can't afford
  (this is technically v1-scope but can ship as a follow-up — the
  bust modal's "leave table" already routes them to a lower stake).

## Files to read first

In rough priority order:

1. **This doc** — design rules above.
2. **`docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2** —
   canonical cash spec. §"Bust semantics" is the old spec the
   sponsorship mechanic replaces; the surrounding sections explain
   why bankrolls + table stacks are kept separate.
3. **`flask_app/routes/cash_routes.py`** — start_cash_session
   + leave_table are where the loan math will live.
4. **`cash_mode/bankroll.py`** — `PlayerBankrollState` dataclass
   gets the two new fields.
5. **`poker/repositories/bankroll_repository.py`** — load/save
   methods get the new fields.
6. **`poker/repositories/schema_manager.py`** — v89 migration
   pattern (mirror v88 right above it).
7. **`flask_app/handlers/game_handler.py`** — `handle_evaluating_hand_phase`
   is where you'd hook a bust-detection point if you wanted
   server-side bust events (current plan uses frontend polling
   on `player.stack`, but server-driven is cleaner — see "Open
   question" below).
8. **`react/react/src/components/game/PokerTable/PokerTable.tsx`** —
   the back-button cash branch is the pattern for the new modal
   to follow. The bust modal lives as a sibling component.
9. **`react/react/src/components/cash/CashModeEntry.tsx`** —
   stake-entry page (where the eventual "filter stakes by
   affordability" lives).

## Suggested commit breakdown (~6 commits)

**Commit 1: Schema v89 — loan fields on player_bankroll_state**
- Migrate `player_bankroll_state` to add `active_loan_amount`,
  `active_loan_rate`.
- Update `PlayerBankrollState` dataclass.
- Update `BankrollRepository.load_player_bankroll` /
  `save_player_bankroll` to round-trip the new fields.
- Tests: schema round-trip; default values for legacy rows.

**Commit 2: Sponsor offers + /api/cash/rebuy**
- Module-level `SPONSOR_OFFERS` dict in `cash_routes.py`.
- New `POST /api/cash/rebuy` route. Validates:
  - Active cash session exists.
  - Player's `Player.stack == 0`.
  - Hand not in progress.
  - Requested amount ≥ table's `min_buy_in`.
  - Bankroll ≥ amount.
- Debits bankroll, updates `Player.stack` via `state_machine.game_state.update_player`.
- Emits SocketIO update (`update_and_emit_game_state(game_id)`).
- Tests: smoke + validation rejections + bankroll persisted.

**Commit 3: Sponsor route + leave-time loan math**
- New `POST /api/cash/sponsor {offer_id}` — looks up offer in
  `SPONSOR_OFFERS`, sets `bankroll.active_loan_amount` /
  `active_loan_rate`, adds `loan_amount` to `bankroll.chips`,
  persists. Returns updated bankroll.
- Player then calls `/api/cash/rebuy` separately (the modal can
  chain the two requests on the "take sponsor offer" path so it
  feels seamless).
- Update `/api/cash/leave` to apply the loan-repay + sponsor-cut
  math described above. Reset loan fields to 0/0.0 after settle.
- Tests: pay-back-fully, partial-repayment-forgiven, no-loan
  fallthrough.

**Commit 4: Frontend bust modal**
- New `BustModal.tsx` in `react/react/src/components/cash/`.
- Polled from `PokerTable.tsx`: detect `player.is_human &&
  player.stack === 0 && !gameState.hand_in_progress`.
- Reads bankroll via `/api/cash/state` (already returns bankroll).
- Three view states keyed on bankroll vs `min_buy_in`:
  - Has enough → rebuy + topup buttons
  - Has some but not enough → "leave to lower stake" CTA
  - Has zero → sponsor offers (3 buttons)
- Clicking sponsor offer calls `/api/cash/sponsor` then
  `/api/cash/rebuy` with the appropriate amount.
- Clicking "Leave table" calls `/api/cash/leave` and navigates to
  `/cash`.

**Commit 5: Stake-entry affordability filter (optional)**
- In `CashModeEntry.tsx`, fetch bankroll on mount.
- For each stake in the picker, disable + grey out if
  `bankroll < bigBlind × 40` (min_buy_in for that stake).
- Show a tooltip on disabled stakes: "Bankroll too small for this
  stake ($X needed)".

**Commit 6: Docs sweep**
- Update `CASH_MODE_AND_RELATIONSHIPS.md` §"Bust semantics" with
  the new sponsorship mechanic.
- Update `NEXT_PHASE_VISION.md` if needed.
- This handoff doc gets a "Status: shipped" note pointing to the
  commit range.

Stop after commit 6 unless the user asks for more. v2 (AI
sponsorship + cross-session persistence + reputation) is its own
handoff.

## Open questions for the implementer

1. **Bust detection: frontend poll vs server-driven event?**
   The current plan polls on `player.stack === 0` from the React
   state. Cleaner would be a SocketIO event from the server
   (`bust_modal_required`) emitted right when the player's stack
   hits 0 between hands. v1 simplest path is the poll; if the UX
   feels laggy, server-driven is a small refactor.

2. **Stake-picker affordability filter — v1 or v2?**
   Listed as "optional" in commit 5 above. Without it, the player
   might try to pick the $1000 table with $400 bankroll, fail at
   sit-down with an error. Acceptable for v1 but a small UX paper
   cut.

3. **What if player tries to leave WITH active loan but ZERO chips?**
   `chips_at_table = 0`, `loan_repaid = 0`, `remaining = 0`,
   `sponsor_cut = 0`. Loan is forgiven (set to 0/0.0). The player
   walks away owing nothing — same as "partial repayment" math
   trivially applied. The v2 reputation mechanic would penalize
   here; v1 lets it slide.

4. **AI personalities as sponsors (v2 design preview).**
   When this lands, each personality's `bankroll_knobs` in
   `personalities.json` should gain fields like
   `lender_profile: {willing: true, max_loan: 1000,
   rate_anchor: 0.40, ...}`. The bust modal pulls eligible AI
   sponsors from the personalities at the table (or the AI pool
   broadly). The loan creates a relationship event
   (`SPONSORSHIP_OFFERED` / `SPONSORSHIP_TAKEN`) feeding the
   `relationship_states` table. Partial repayment / default
   damages the lender's `respect` / `likability`. **This is the
   high-leverage feature; v1 is the foundation it builds on.**

5. **Tunable numbers — should they be in a DB table or hardcoded?**
   For v1 a module-level dict in `cash_routes.py` is fine. v2 can
   move to a settings table if/when personality-specific terms
   need DB lookups.

## Test patterns to follow

- `pytestmark = pytest.mark.integration` for tests needing the
  DB (matches the existing cash test convention).
- `tests/test_cash_mode/test_seating.py` is the closest prior art
  for accounting-math tests — the loan-repay calculation belongs
  there or in a new `test_loan_math.py`.
- `tests/test_repositories/test_bankroll_repository.py` covers
  the bankroll schema round-trip; new fields go there.
- Frontend: no unit tests for the bust modal — manual playtest
  is the bar for v1, same as the rest of cash UI.

## Operating notes inherited from cash v1

- **Branch: `phase-1`** — same branch the rest of cash work has
  shipped on. Pull before starting.
- **`docker compose restart backend`** after Python changes —
  inotify hot-reload is broken in this dev environment, so manual
  restart is mandatory.
- **Tests run in Docker**: `python3 scripts/test.py -k <pattern>`.
- **TypeScript check**: `python3 scripts/test.py --ts`.
- **Codex assist** for mid-implementation review:
  `codex-assist ask "..." -C /home/jeffh/projects/my-poker-face --name <tag>`.
  Worth a check between commits 3 and 4 — the leave-time loan
  math is the most bug-prone surface.

## References

- Cash mode v1 handoff (predecessor): `CASH_MODE_V1_HANDOFF.md`
- Canonical spec: `CASH_MODE_AND_RELATIONSHIPS.md` Part 2
- Relationship layer (will be the v2 sponsorship payoff):
  `RELATIONSHIP_PHASE_3_HANDOFF.md`
- Commit range to scan for cash v1 context:
  `613c0e9b`..`08b50900` on `phase-1`.
