---
purpose: Implementation handoff for cash mode Path A — close the v1 economic loop so AI bankrolls reflect real session P&L, and expose per-personality bankroll knobs in the admin UI
type: guide
created: 2026-05-18
last_updated: 2026-05-18
---

# Cash Mode — Path A Handoff: AI Cash-Out + Bankroll Knobs Admin UI

This doc covers the close-the-loop work for AI bankrolls in cash
mode v1. Today, AI bankrolls only ever decrement (at sit-down) and
regen passively via `project_bankroll` — winnings at the table never
land back in the AI's bankroll, and per-personality knobs aren't
editable outside hand-editing `personalities.json`. Path A fixes
both.

This is the **foundation for Path B (AI-opponent sponsorship)** —
Napoleon can't decide whether to lend you $500 unless his bankroll
reflects what he actually won/lost at the table recently.

## Current state (what's already in place)

- `ai_bankroll_state` table exists (schema v88) keyed by `personality_id`.
- `cash_mode.bankroll.AIBankrollState` dataclass + `project_bankroll`.
- `BankrollRepository.load_ai_bankroll` / `save_ai_bankroll` /
  `load_ai_bankroll_current` (projection-on-read).
- `BankrollRepository.load_personality_knobs` /
  `save_personality_knobs` (round-trips through `config_json.bankroll_knobs`).
- All 53 personalities seeded with hand-tuned knobs.
- AI bankrolls are read at two sites: `_build_cash_game`
  (`flask_app/routes/cash_routes.py:_build_cash_game`) and
  `_refill_cash_seats` (`flask_app/handlers/game_handler.py:616`).
- AI bankrolls are written at two sites: same two sites at sit-down,
  always debiting `buy_in`.

## The gap

**No AI bankroll credit-back path.** When an AI wins chips at the
table:

  1. Their `Player.stack` grows during play.
  2. They keep playing (cash sessions don't end on win).
  3. If they bust → seat replaced via `_refill_cash_seats`; **winnings
     evaporate** (`Player.stack` was non-zero before the bust, but
     between hands the all-in loss left them at 0; the `stack > 0`
     state never crossed back to bankroll).
  4. If the player leaves the session → `delete_game` wipes the
     `game_data`; **all remaining AI table stacks evaporate**.

This means AI bankrolls drift downward monotonically and only recover
via `project_bankroll`'s daily regen. From an economic-state-tracking
perspective, the table is a black hole.

## Design — Path A

### A.1: AI cash-out on bust (between-hands)

When `_refill_cash_seats` removes a busted AI seat, the AI's `Player.stack`
is 0 by definition. **No cash-out needed for the busted AI** — the
buy-in was debited at sit-down, they lost it. Already correct.

But we should also handle a subtler case: **AI stops playing
voluntarily** (e.g., stop-loss / stop-win trigger, Path C; or
relationship-driven stand-up, future). For v1, this case doesn't
fire — AIs are bust-only. But the **call site for the cash-out**
should be added now, even if its trigger condition is "never" in v1:

```python
def _ai_stand_up_with_cashout(seat_idx, personality_id, game_data, state_machine):
    """Credit the AI's current Player.stack back to their bankroll
    and remove them from the seat. Caller decides why (bust, stop,
    relationship event). v1 callers: none yet — function exists for
    Path B/C to invoke."""
```

### A.2: AI cash-out on player leave

When `/api/cash/leave` fires, **every seated AI's current stack credits
back to that AI's bankroll**. The session ends; whatever they had at
the table is theirs.

This is the load-bearing v1 change. Concrete logic:

```python
# In flask_app/routes/cash_routes.py:leave_table, after settling
# the player's loan but before delete_game:

cash_personality_ids = game_data.get('cash_personality_ids', {})
now = datetime.utcnow()
for player in state_machine.game_state.players:
    if player.is_human:
        continue
    pid = cash_personality_ids.get(player.name)
    if not pid:
        continue
    stored = bankroll_repo.load_ai_bankroll(pid)
    if stored is None:
        continue  # AI was seated without a row — shouldn't happen
    # Project forward to current time, then credit table stack on top.
    knobs = bankroll_repo.load_personality_knobs(pid)
    projected = project_bankroll(stored, knobs.bankroll_cap, knobs.bankroll_rate, now)
    new_chips = min(knobs.bankroll_cap, projected + player.stack)
    bankroll_repo.save_ai_bankroll(AIBankrollState(
        personality_id=pid,
        chips=new_chips,
        last_regen_tick=now,
    ))
    logger.info("[CASH] AI cash-out %r: +%d → %d (cap %d)",
                pid, player.stack, new_chips, knobs.bankroll_cap)
```

**Cap clamp** is important: if Napoleon was at $200k bankroll, won
$50k at the table, his bankroll should clamp to his cap (250k for
the highest-cap personalities, 10k for the median). The cap is a
**hard ceiling** — extra winnings vanish. This is intentional: it
prevents one AI from accumulating a runaway bankroll relative to
the rest of the cast.

### A.3: AI cash-out on mid-hand player abandonment

If the player closes the tab during a hand, the game persists in
`game_state_service` for the TTL (~2hr). Eventually the session
expires. When it does, **AI cash-out should fire** as if the player
had called `/api/cash/leave`.

Two options:
- **Option 1 (recommended):** Add a TTL-expiry hook in
  `game_state_service` that fires the same cash-out logic as
  `/api/cash/leave`. Cleanest — same code path.
- **Option 2:** Treat session expiry as "AI keeps the chips" — the
  game just disappears. Simpler but feels like a punishment for the
  player (winnings the AIs accumulated during play go to them, but
  if the player times out their stack also vanishes... wait, that
  matches current `/api/cash/leave` behavior. The player gets their
  stack back; AI gets theirs back.).

Pick Option 1. It's the consistent rule: **session-end always
cashes everyone out.**

### A.4: Per-personality bankroll knobs admin UI

Currently knobs are editable only by hand-editing `personalities.json`
and re-seeding. Add UI in the personalities admin so a tester can
tune `bankroll_cap` / `bankroll_rate` / `buy_in_multiplier` /
`stake_comfort_zone` live.

**Backend (~1 file):** Add a route in `flask_app/routes/personality_routes.py`
(or wherever personality CRUD lives — check first):

```python
@personality_bp.route("/api/personalities/<personality_id>/bankroll-knobs", methods=["GET", "PUT"])
def bankroll_knobs(personality_id: str):
    """GET → current knobs (with defaults filled in).
    PUT body: partial knobs dict → merge + save via save_personality_knobs."""
```

Admin-only (require_permission decorator).

**Frontend (~1 component):** Add a section to the existing
personality-edit page (find via `grep -r PersonalityEditor react/`).
Six fields:
- `bankroll_cap` (number input)
- `bankroll_rate` (number input, label "chips/day regen")
- `buy_in_multiplier` (number input, decimal, hint "× min_buy_in")
- `stop_loss_buy_ins` (number, hint "v2 — unused in v1")
- `stop_win_buy_ins` (number, hint "v2 — unused in v1")
- `stake_comfort_zone` (select, options from STAKES ladder, hint "v2 — unused in v1")

Show **current live bankroll** alongside (read via
`load_ai_bankroll_current`) so the admin can see the AI's actual
state. Add a "Reset bankroll to cap" button for testing.

## Suggested commit breakdown (~4 commits)

**Commit 1: AI cash-out on player leave**
- Add the loop in `leave_table` between loan settlement and `delete_game`.
- Tests in `tests/test_cash_mode/test_ai_cashout.py`:
  - Single AI with non-zero stack → bankroll grows by stack amount, clamped to cap.
  - AI at cap before cash-out → cap stays at cap (cap is hard).
  - Multiple AIs with mixed stacks → each credited independently.
  - AI with 0 stack (busted same hand as leave) → no-op.
  - `cash_personality_ids` missing entry → skip that AI (log a WARN).

**Commit 2: AI cash-out helper for stand-up (callable, unused in v1)**
- Pure function `cash_mode/seating.py:cash_out_ai_seat(table, ai_bankroll, knobs, now) -> (new_table, new_ai_bankroll)`.
- Unit tests: pure-function math (matches the leave-time loop logic above).
- Re-export from `cash_mode/__init__.py` so Paths B/C can import it.

**Commit 3: Admin route — bankroll knobs CRUD**
- GET + PUT route as described.
- Tests: round-trip a partial knob update; defaults filled in for
  missing fields; admin permission required.

**Commit 4: Admin UI — bankroll knobs section in personality editor**
- Six field inputs + current-live-bankroll readout + reset button.
- Reuse existing form patterns from PersonalityEditor.
- No frontend unit tests for v1 — manual smoke check.

## Open questions

1. **Cash-out cap policy.** Hard clamp at `bankroll_cap` is the
   v1 rule above. Alternative: allow temporary over-cap (AI carries
   the excess for a few days, then it bleeds back via reverse regen).
   v2 territory; ship hard clamp for now.

2. **Should AI cash-out fire telemetry?** A `bankroll_event` log
   line per AI per session would let us analyze the economy after
   playtest. Cheap; recommended.

3. **What about AI bankroll on mid-hand session-end?** Path A.3
   above. Confirm Option 1 (TTL hook) is the right call vs simpler
   "AIs keep their stacks if you time out."

## Test patterns

- `pytestmark = pytest.mark.integration` for routes/DB-touching tests.
- `tests/test_cash_mode/test_seating.py` for accounting-math
  patterns; add `test_ai_cashout.py` alongside.
- For the leave-time AI cash-out test, mirror the existing
  `tests/test_cash_mode/test_loan_settlement.py` shape — tempdb,
  seeded bankrolls, assert before/after deltas.

## Files to read first

1. **This doc** — design above.
2. **`flask_app/routes/cash_routes.py:leave_table`** — where the
   new cash-out loop goes.
3. **`flask_app/handlers/game_handler.py:_refill_cash_seats`** —
   the existing pattern for AI bankroll mutations between hands.
4. **`poker/repositories/bankroll_repository.py`** —
   `save_personality_knobs` is already wired for the admin UI.
5. **`react/react/src/components/admin/`** (find PersonalityEditor)
   — pattern to mirror for the new bankroll-knobs section.
6. **`docs/plans/CASH_MODE_AND_RELATIONSHIPS.md`** §"Bankroll knob
   storage" (line 526) and §"Bankroll regen (pure projection on
   read)" (line 530) — the canonical spec.

## Why ship A before B

Path B's lender-eligibility check ("does Napoleon have $500 to lend
you?") reads `load_ai_bankroll_current`. Today that's a roughly-
fictional number because winnings evaporate. Without A:
- Napoleon's lender capacity drifts down over a session even when
  he's winning.
- Sponsor offers from AIs become unpredictable — "Napoleon at the
  table with $50k in chips can't lend you $500 because his bankroll
  hasn't credited the winnings."
- Reputation/credit-damage in B becomes pointless because there's
  no real wealth to damage.

A is small. B without A is built on sand.
