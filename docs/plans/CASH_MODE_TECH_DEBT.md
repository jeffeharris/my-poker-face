---
purpose: The authoritative paydown ledger for cash-mode state-machine tech debt — the authority model, every reconciler's exact retirement gate, and the ordered sequence to finish demoting the legacy stores
type: design
created: 2026-06-01
last_updated: 2026-06-01
---

# Cash Mode — State-Machine Tech-Debt Ledger

Two authoritative state machines landed and are flipped on dev (committed default
OFF, prod untouched):

- **Presence** (`entity_presence`, `PRESENCE_AUTHORITY_ENABLED`) — owns **where an
  actor is** (seated / idle / off-grid). Built + flipped.
  See `CASH_MODE_PRESENCE_PHASE3_FLIP.md`.
- **Chip custody** (`chip_ledger_entries`, `CHIP_CUSTODY_ENABLED`) — owns **chips**
  (bankroll derivable, conservation enforced). Phases 1-3 + deletion-integrity
  flipped. See `CASH_MODE_CHIP_CUSTODY_HANDOFF.md`.

This doc is the single source of truth for **what's still legacy and the exact
condition to retire each piece.** Read it before adding any new reconciler — if
you're tempted to write one, the right fix is almost always to finish a demotion
below, not to add another store-disagreement repair.

## 1. The authority model (state this, don't re-derive it)

| Fact | Authority | Legacy cache (still written) | Status |
|---|---|---|---|
| Who is seated / idle / off-grid | `entity_presence` | `cash_tables.seats` (occupancy half), `cash_idle_pool`, `ai_side_hustle_state`, `ai_vice_state` | authority flipped on dev; caches still written |
| Committed chips (bankroll, at-seat custody) | `chip_ledger_entries` (`balance_of`) | `ai_bankroll_state.chips` / `player_bankroll_state.chips` (verified cache) | authority flipped on dev; int is a cache |
| Live in-hand stack | `cash_tables.seats[].chips` | — | **legitimately lives here** (see §4) |
| Hand state (cards, board, pot, RNG) | `games.game_state_json` | — | not part of either machine |

**The cache rule:** `cash_tables.seats`, `cash_idle_pool`, and the bankroll ints
are **projections/caches** of the two authorities, not independent truth. Nothing
new may treat them as authoritative. They are still *written* only because the
storage demotion (§3) hasn't landed.

## 2. Done this round (2026-06-01)

- Chip custody Phases 1-3 + persona-delete settle (conservation-safe deletion).
- Dev DB backfilled → `LEDGER COMPLETE` (AI 1239/1239 + Player 4/4).
- **Dead code deleted:** `cleanup_orphan_cash_games` (no callers, v1.5-deprecated);
  `record_cap_clamp` (no live callers — the `'cap_clamp'` reason string stays in
  `LEDGER_REASONS` for historical-audit queries).
- **Verified NOT dead (kept):** `_purge_other_cash_rows` — has 4 live callers
  (sit + leave paths); an inventory pass mis-flagged it.

## 3. The remaining demotion (the expensive 20%) — ordered paydown

Do these in order; each unblocks the reconciler retirements in §5. None is
"working-now" urgent — the bug classes are already structurally closed by the
authorities. This is debt reduction so the system is clean to build on.

### Step A — Presence read-side completion (occupancy)
Make the seat-occupancy + idle READS derive from `entity_presence` (not
`cash_tables.seats` / `cash_idle_pool`), then stop writing the occupancy half of
those caches. Already evidence-gathered + held: `CASH_MODE_PRESENCE_READSIDE_COMPLETION.md`.
**Unblocks:** `_free_ghost_human_seats`, `_reclaim_zombie_casino_seats`,
`whereabouts.py` → trivial read, the `_shadow_reconcile_table` / call-site
`shadow_transition` dead-under-authority code.

### Step B — Decide the live-stack ↔ ledger relationship (chips), THEN demote seat chips
**This is the blocker the original handoff glossed over.** `cash_tables.seats[].chips`
is the LIVE stack (changes every hand via the engine); the ledger `seat:` balance
only moves at buy-in/cash-out (per-hand P&L is unledgered by design — it nets in
the seat balance, settles at cash-out). They agree only at **session boundaries**.
So the seat chips CANNOT be a pure derived view of the ledger mid-hand. Pick one:
  - **(B1) Accept `cash_tables.seats[].chips` as the live-stack cache** (recommended;
    chosen 2026-06-01). The ledger owns *committed* custody; the live stack is a
    legitimate working value, not duplication. No further chip demotion — just keep
    the cache rule (§1) enforced. Lowest risk.
  - **(B2) Ledger per-hand P&L** so the `seat:` balance == live stack always, then
    make `cash_tables.seats[].chips` a true derived VIEW. Adds per-hand SQLite cost
    on the ticker hot path (`CASH_MODE_CHIP_CUSTODY_SCOPE.md` §"Sim hot-path cost"
    warns against this). Only do this if a future feature needs the live stack
    ledger-derivable.

Under (B1) the chip side is **done** — there is no further seat-chip demotion, and
§5's chip-related retirements are reframed as Presence (occupancy) retirements.

### Step C — Atomic seat-map demotion (only if pursuing more than B1)
If/when both read-sides derive, `cash_tables.seats` becomes a pure projection with
NO independent writer. Measured surface (2026-06-01): **23 `save_table` callsites**,
~8 `slot['chips']` readers, 14 `archetype` readers, 3 `seated_at`. Must land as ONE
migration — a half state has two seat writers, which is the ghost-seat bug. The
genuinely-table-own data (exists / stake level / teardown) stays as plain config.
Move `archetype` / `seated_at` to a `seat_state` satellite if keeping them.

## 4. Why the live stack staying in cash_tables.seats is NOT debt

It's tempting to call `cash_tables.seats[].chips` duplication. It isn't: it's the
**only** home for in-hand stack state, and the ledger deliberately doesn't track
per-hand P&L. Treating it as the live-stack cache (B1) is a design decision, not a
shortcut. The debt is only the *occupancy* duplication (Step A) and, if ever wanted,
the per-hand-P&L ledgering (B2).

## 5. Reconciler retirement register

Each retires when its gate clears. **Do not delete before the gate** — these bridge
real split-brain windows while the caches are still written.

| Reconciler | File | Repairs | Retirement gate |
|---|---|---|---|
| `_free_ghost_human_seats` | cash_routes.py | human seat survives a deleted game row | **Step A** (presence-backed occupancy read covers it) |
| `_reclaim_zombie_casino_seats` | casino_provisioning.py | seat holds a deleted/un-stamped persona | **Step A** (+ persona-delete settle now handles the chip half) |
| `whereabouts.py` | cash_mode/whereabouts.py | unions 4 stores to name `seated_and_idle` etc. | **Step A** → degrades to a trivial `entity_presence` read |
| `_shadow_reconcile_table` + call-site `shadow_transition` (seat sites) | lobby.py / routes | mirror seats → entity_presence when authority OFF | **dead once `PRESENCE_AUTHORITY_ENABLED` is the committed default** (currently early-return no-ops under authority; kept for the flag-off / prod path) |
| `_boot_sweep_stale_cash_rows` | lobby.py | GC abandoned `cash-*` rows; now settles seat chips first (Phase 3) | **keep** — it's a legitimate GC/janitor (TTL eviction), not a store-disagreement repair. Not debt. |
| `_restore_cash_table_binding` | game_handler.py | cold-load lost `cash_table_id` (memory-only field) | **keep until** the binding is persisted in `game_state_json` or read from presence on every load (already prefers presence under authority) |
| `_warm_cash_game_for_leave` | cash_routes.py | rehydrate a DB-only game so leave settles the real stack | **keep** — handles the legitimate server-restart memory-miss; not a store disagreement |
| `_drain_fish_bankroll_to_pool` | casino_provisioning.py | return pool-funded fish chips on exit | **keep** — real closed-economy accounting, now ledgered; not debt |
| `_purge_other_cash_rows` | cash_routes.py | one cash row per owner | **keep until** the session machine (§6) owns the one-active-session invariant |

## 6. Adjacent machines never started (scoped in §4 of the state model)

- **Session coordinator** (`cash_sessions`): vocabulary exists
  (`active/paused/abandoning/closed/broken`) but the reserved `paused`/`abandoning`
  states and a real **disconnect → pause → resume** flow are NOT wired. Cut 1's
  freeze-guard is the behavioural stand-in. Promoting it would own the
  one-active-session invariant (retiring `_purge_other_cash_rows`).
- **Stake / backing lifecycle** (`active/carry/settled/defaulted`): not migrated
  onto the transition-log substrate.

## 7. Intentionally kept (NOT debt — don't "clean up")

- `REGEN_ENABLED` + `project_bankroll`'s regen branch — retired-but-kept A/B lever
  (passive faucet vs side hustle). Default OFF.
- The three cutover flags (`PRESENCE_*`, `CHIP_CUSTODY_*`) — kill switches; keep
  until each machine is the committed prod default.
- `'cap_clamp'` reason string — historical audit queries.

## 8. The one rule for new code

Before writing anything that reads `cash_tables.seats` occupancy, `cash_idle_pool`,
or a bankroll int as TRUTH: don't. Read the authority (`entity_presence` /
`balance_of`). Before writing a reconciler: don't — finish the matching demotion in
§3 instead. This is how the debt stays paid instead of regrowing.
