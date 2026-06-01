---
purpose: Grounded narrative log of diagnosing + fixing the cold-load cash-seat orphan (lobby shows my seat taken; Resume opens different players)
type: reference
created: 2026-05-29
last_updated: 2026-05-29
---

# Captain's log — cold-load cash-seat orphan (development worktree)

Honest record of chasing a "my seat got taken / Resume shows different
people at The Back Room" report. Newest at the bottom.

---

## 2026-05-29 — the orphaned seat

**Report.** Player has a seat at The Back Room (circuit/cash mode) but the
lobby shows it as full with 6 people — their seat looks taken. Clicking
Resume opens the table with a *different* set of players.

**First instinct, then the correction.** My initial fan-out trace flagged a
concurrency window between the lobby's `list_all_tables` read and the resume
re-read (ticker refilling between them). That was a real seam but the WRONG
diagnosis for this symptom — it explains "roster changed slightly," not "my
own seat is gone and the whole table is strangers." Grounding in the live DB
fixed the aim: the lobby card seats come *entirely* from the `cash_tables`
row; the live game is only consulted for emotions. So the row had genuinely
lost the human seat.

**What the data showed.** `table_id` is per-sandbox, not global — every
player has their own `cash-table-2-001` "Back Room." guest_jeff's active
session (`cash_sessions`, `ended_at NULL`) said sandbox `4db9b9f2`, seat 4.
That sandbox's row had 6 AIs, seat 4 = some AI. The live game
`cash-P3lh4jfkwgd4d8ezJgQ8Fg` had a totally different roster
(Agatha/Tesla/AI 13/Jeff/Cheshire/Obama) and — the smoking gun —
**`cash_table_id: None`** in `game_state_json`.

**Root cause.** `cash_table_id`/`cash_seat_index` are memory-only fields,
never serialized into `game_state_json`. The `/api/game-state` cold-load
block (`game_routes.py:958`) restores them from the durable `cash_sessions`
row — but *only that route does*. A game that advances a hand after eviction
without passing through it keeps `cash_table_id=None`, so the hand-boundary
sync (`game_handler.py:_refresh_lobby_table_for_session`) hits its
`if not table_id: return` and never re-stamps the human seat. The seat then
looks orphaned, `refresh_unseated_tables` (which skips a table only while
`human_seat_index() is not None`) treats it as empty, and the global greedy
fill packs all 6 seats with AIs. Same cold-load-divergence / ghost-seat class
as the orphan-double-settle and cold-session-wedge scars.

**Fix (root, design-consistent).** Added `_restore_cash_table_binding`: the
hand-boundary hook now self-heals the binding from `cash_sessions` (mirroring
the fallback `leave_table` already had at `cash_routes.py:4563`) and writes it
back onto `game_data`. Once restored, the hook keeps the human seat stamped
each hand → `refresh_unseated_tables` keeps skipping the table. 4 unit tests
in `test_live_seated_protection.py`; 843 cash tests green.

**Data repair — the conservation trap.** The already-damaged row needed
fixing too (the hook can't reclaim a seat that's *already* an AI, and its
reconciliation would have leaked the 6 phantom AIs' chips). The phantom AIs
were seated by the greedy fill, which *debits their bankroll for the buy-in*
(`debit_bankroll_for_seat` — a pure bankroll→seat transfer). So a blind
overwrite would vaporize ~900 chips. Conservation-correct repair
(`scripts/reconcile_back_room_orphan.py`, dry-run default, WAL-safe backup):
credit each phantom AI's *current* seat chips back to its bankroll (the exact
inverse pure transfer, no ledger row), then rebuild the row to mirror the live
game (human at seat 4 + the 5 live AIs). Ran it with the **backend stopped** —
the dry-run caught the ticker churning the orphaned table's roster every 2s
(the phantom set changed between two reads), which would have re-clobbered any
live write. Restarted; row held through ticker cycles because seat 4 is now
`human`.

**Lessons.** (1) Match the diagnosis to the *exact* symptom — "roster drifts"
≠ "my seat is gone." (2) The lobby card is the `cash_tables` row, full stop;
the live game is a separate truth synced only at hand boundaries. (3) Any
AI-seat teardown is a chip-conservation operation — the greedy fill debited
bankrolls, so removal must credit them back. (4) Stop the ticker before
hand-editing a live cash row.
