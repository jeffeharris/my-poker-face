---
purpose: Implementation handoff for cash-mode lobby v1 — persistent multi-table state, table list with AI rosters, seat-picker UX. The "see who's at each table, pick an open seat" piece, without yet doing live background simulation.
type: guide
created: 2026-05-18
last_updated: 2026-05-18
---

# Cash Mode — Lobby v1 Handoff: Multi-Table View + Seat Picker

This is the **v1.5 step** between current single-table cash mode
and the full Path C (background sim with AI movement). It ships
the player-facing feature you wanted — *see who's at each table,
pick an open seat* — without yet building the live AI-only hand
simulator.

> **What this is not:** AI-only hands while you're away. Tables
> don't run by themselves. AIs don't switch tables mid-session.
> Those land in full Path C; this lobby is the data + UI
> foundation they'll need.

## Prerequisites

- Path A shipped ✅ (AI bankrolls credit on leave — rosters can
  reflect real economic state).
- Path B shipped ✅ (relationship layer + AI lender tracking —
  rosters can show personality-specific relationship hints).

Both already on `phase-1` at the time of writing.

## What the player will see

`/cash` becomes a **table list** instead of a stake picker. Each
stake shows one **table card** with:

- Stake label + buy-in range
- **4 seated AIs** (avatars + names, possibly with a small icon if
  there's an outstanding loan between you and them)
- **2 open seats** — one is the player's intent slot, the other
  is the "live fill" slot that another AI may walk into during
  the session
- Player's affordability state for this table (afford / sponsor-
  needed / locked) — same tri-state from current entry screen

Tap an open seat → if affordable, sit straight down; if sponsor-
required, open the existing SponsorModal (now with the table's
specific personality lenders eligible).

The current "stake card" UI is the seed; this is essentially
the same component with a roster strip showing the 4 seated AIs
and a 2-open-seat indicator.

## What the system needs

### 1. Persistent table state

A new `cash_tables` SQLite table (schema v91). One row per
"named" table.

```sql
CREATE TABLE cash_tables (
    table_id TEXT PRIMARY KEY,           -- e.g., "cash-table-$2-001"
    stake_label TEXT NOT NULL,
    seats_json TEXT NOT NULL,            -- JSON array of slots
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

`seats_json` is an array of 6 slot entries (4 AI baseline + 2 open):

```jsonc
[
  {"kind": "open"},                                              // intent seat
  {"kind": "open"},                                              // live-fill seat
  {"kind": "ai", "personality_id": "napoleon", "chips": 1240},
  {"kind": "ai", "personality_id": "warren_buffett", "chips": 870},
  {"kind": "ai", "personality_id": "donald_trump", "chips": 540},
  {"kind": "ai", "personality_id": "jay_gatsby", "chips": 1810}
]
```

Slot kinds:
- `"open"` — empty seat, eligible for player or live-fill AI
- `"ai"` — AI personality + their persisted table chips
- `"human"` — player currently seated (set transiently while a
  session is live; reverts to `"open"` on leave)

**Per-seat chips are persisted.** When the player sits, those
values become the AIs' starting stacks at that game's hand 1.
When the player leaves, the game's ending stacks are written back.
**This is the key state coupling**: an AI who wins big on you
keeps those chips for the next player who sits down. (Or — per the
movement rules below — they may stake-up to a higher table and
take those chips with them.)

**One personality per active seat.** Hard rule: a personality
appears in at most one `cash_tables.seats_json` row's AI slots.
Enforced by lobby maintenance — `ensure_lobby_seeded` and
`refresh_table_roster` both check the global "currently seated"
set before placing.

### 2. Lobby maintenance

**Three movement helpers** form the core of "feel alive":

**a) `ensure_lobby_seeded()`** — idempotent boot routine:
- Creates one table per stake (`$2`, `$10`, `$50`, `$200`,
  `$1000`) if missing.
- Fills each table's **4 baseline AI seats** with eligible
  personalities (existing `list_eligible_for_cash_mode` +
  per-personality affordability check).
- Sets each AI's `chips` to their per-personality buy-in.
- Each personality lands on at most one table (global uniqueness
  check during fill).

**b) `evaluate_ai_movement(table, ai_seat)`** — pure function,
runs at each AI's hand boundary. Returns `'stay' | 'stake_up' |
'take_break' | 'forced_leave' | 'bored_move'`. Inputs: the AI's
table chips, their buy-in, projected bankroll, current stake.
Decision tree per locked decision #1:

```python
def evaluate_ai_movement(ai, buy_in, projected_bankroll, stake_idx, rng):
    if ai.chips <= 0.3 * buy_in:
        return 'forced_leave'                    # busted or near-bust
    if ai.chips >= 2.0 * buy_in:
        if stake_idx + 1 < len(STAKES) and projected_bankroll >= STAKES[stake_idx + 1].min_buy_in:
            if rng.random() < 0.30:
                return 'stake_up'                # 30% chance to climb
        if rng.random() < 0.10:
            return 'take_break'                  # 10% chance to break
        return 'stay'
    if rng.random() < 0.015:                     # ~1.5% per hand
        return 'bored_move'
    return 'stay'
```

**c) `refresh_table_roster(table)`** — applies movement decisions
to a table's seats:
- For each AI seat: evaluate movement; if not `'stay'`, remove
  the AI (move to idle pool with appropriate metadata) and set
  the seat to `"open"`.
- For each `"open"` seat: roll the **live-fill** probability
  (15% baseline per refresh tick). If fill triggers, pick an
  eligible AI from the idle pool or the never-seated pool, set
  their chips to their buy-in, and place them.
- Bump `last_activity_at`.

**Cadence**:
- For tables with an active player → called at each hand boundary
  via `handle_evaluating_hand_phase` hook (alongside
  `_refill_cash_seats` and `_detect_human_cash_bust`).
- For tables without a player → called lazily at each
  `GET /api/cash/lobby` request (cheap; no background daemon
  needed in v1.5). The lobby refreshes as a side effect of being
  observed, which is "good enough alive" without a real-time sim.

### 2b. Idle pool

AIs not currently at a table. Implementation: either a separate
`cash_idle_pool` table or a `last_seated_at` timestamp on
`ai_bankroll_state`. Recommendation: **separate table** for
schema clarity. Schema v92:

```sql
CREATE TABLE cash_idle_pool (
    personality_id TEXT PRIMARY KEY,
    left_at TIMESTAMP NOT NULL,
    reason TEXT NOT NULL,                -- 'forced_leave' | 'stake_up_queued' | 'take_break' | 'bored_move'
    target_stake TEXT                    -- non-null if 'stake_up_queued', else NULL
);
```

Idle pool re-entry runs at the same lobby-read tick: for each
idle AI whose `left_at` is ≥ 3 minutes ago AND ≤ 10 minutes ago,
roll a per-tick re-entry chance (~10%). On success, find a table
with an open seat at the AI's preferred stake (or one tier down
30% of the time) and place them.

### 3. Lobby read API

GET `/api/cash/lobby` → returns:

```json
{
  "bankroll": 1240,
  "tables": [
    {
      "table_id": "cash-table-$2-001",
      "stake_label": "$2",
      "min_buy_in": 80,
      "max_buy_in": 200,
      "affordability": "affordable" | "sponsor_eligible" | "locked",
      "seats": [
        {"kind": "human", "open": true},
        {"kind": "ai", "personality_id": "napoleon", "name": "Napoleon",
         "avatar_url": "...", "chips": 1240,
         "relationship_hint": "wary of you"}
      ]
    }
  ]
}
```

`relationship_hint` is the same surface Path B's SponsorModal uses
— `project_heat` + relationship state read.

### 4. Sit-down route

POST `/api/cash/sit` body: `{table_id, seat_index}` — replaces
the current `/api/cash/start`. Validations:
- `table_id` exists
- `seat_index` is the human slot AND it's open (`personality_id ==
  null`)
- Player can afford the buy-in (else 4xx → frontend opens
  SponsorModal with this table's eligible personality lenders)
- Player has no active session

Sit-down semantics:
- Mark the seat occupied (`kind: "human", personality_id: <owner_id>`)
- Persist that immediately so a second device can't double-sit
- Create the cash game using the table's CURRENT roster + chip
  counts (no fresh randomization)
- AIs sit with their persisted chip counts, NOT a fresh buy-in
- Player's stack = the player's chosen buy-in (or sponsor amount)

POST `/api/cash/leave` (existing route) gains the roster-refresh
side-effect described above, runs **after** the existing AI
cash-out + loan settlement steps. The seat is freed (`kind:
"human", open: true`), AI seats persist their final chip counts.

### 5. SponsorModal: filter personality offers by this table's roster

Path B's SponsorModal currently shows lenders from a pool
(however the route was implemented — check `compute_personality_offers`).
Lobby v1 narrows the pool to **AIs at the table you're trying to
join**. Reasonable model: a personality only lends if they're going
to be at the table watching you play. If none of that table's AIs
qualify, fall back to house archetypes.

Single-line config change in the offer generator.

## Suggested commit breakdown (~8 commits)

**Commit 1: Schema v91 — cash_tables**
- Idempotent CREATE TABLE IF NOT EXISTS.
- New repo: `poker/repositories/cash_table_repository.py` with
  `load_table`, `list_all_tables`, `save_table`.
- `CashTableState` dataclass in `cash_mode/tables.py` (6 slots,
  typed slot dicts: `open` | `ai` | `human`).
- Tests: schema round-trip, JSON seat serialization, defaults.

**Commit 2: Schema v92 — cash_idle_pool**
- Idempotent CREATE TABLE IF NOT EXISTS for `cash_idle_pool`
  (personality_id PRIMARY KEY, left_at, reason, target_stake).
- Repo additions: `load_idle`, `list_idle`, `save_idle`,
  `delete_idle`.
- Tests: schema + round-trip + reason enum coverage.

**Commit 3: `evaluate_ai_movement` + `refresh_table_roster` pure
helpers**
- Pure-function module `cash_mode/movement.py` —
  `evaluate_ai_movement(ai, buy_in, projected_bankroll, stake_idx, rng)`.
- Pure-function `refresh_table_roster(table, idle_pool, rng,
  now, knobs_loader)` returning `(new_table, idle_pool_changes)`.
- Live-fill probability driven by a tunable constant (default
  0.15 per tick).
- Pure tests: each movement branch (stay/stake_up/take_break/
  forced_leave/bored_move), live-fill triggers, idle-pool
  re-entry, global "one personality per active seat" invariant.

**Commit 4: Lobby seeding + boot hook**
- `ensure_lobby_seeded()` — idempotent: creates 5 tables (one
  per stake) if absent, fills 4 AI seats per table with eligible
  personalities at buy-in chips, leaves 2 seats `"open"`.
- `kill_all_cash_sessions()` — boot-time cleanup that calls
  `leave_table` semantics on every in-flight cash session before
  seeding the lobby (replaces the old `cleanup_orphan_cash_games`
  approach; safe because cash sessions are in-memory only).
- Wired into app startup. Backend restart confirms lobby exists
  (`SELECT * FROM cash_tables` shows 5 rows).

**Commit 5: GET /api/cash/lobby**
- Reads all tables + player bankroll + computes affordability
  per table + attaches relationship hints per AI seat.
- **Triggers `refresh_table_roster` for each unseated table**
  before returning — this is how unseated tables stay alive
  without a daemon. Side-effecting on a read endpoint is
  intentional and documented; the alternative (background ticker)
  defers to full Path C.
- Tests: tempdb with seeded lobby, asserts shape + affordability
  tri-state + relationship hint pass-through; verifies
  movement-on-read updates persistence.

**Commit 6: POST /api/cash/sit (replaces /api/cash/start)**
- Validation: table exists, seat open, affordability check,
  no active session.
- Reuses `_build_cash_game` but with the table's persisted AI
  roster + chip counts (not fresh randomization).
- Sets the chosen seat to `"human"`; persists immediately so
  double-sit is impossible.
- Tests: happy path, double-sit rejection, unaffordable rejection
  (→ 402 with `requires_sponsor: true` and lender preview).

**Commit 7: Hand-boundary refresh hook + sponsor narrowing**
- `handle_evaluating_hand_phase` (game_handler.py) gains a call
  to `refresh_table_roster` for the player's table — applies
  movement decisions and the live-fill probability AFTER
  `_refill_cash_seats` and `_detect_human_cash_bust`. AI joins
  mid-session route through the existing `_refill_cash_seats`
  controller-rebuild path.
- `/api/cash/leave` persists end-of-session chip counts before
  the existing settlement, then applies a final
  `refresh_table_roster` pass.
- `compute_personality_offers` route caller scoped to the
  current table's AI roster; falls back to house archetypes if
  zero qualify.
- Tests: leave + re-read lobby shows updated chip counts; mid-
  session live fill triggers a new seat; sponsor narrowing
  produces table-roster lenders when eligible.

**Commit 8: Frontend — lobby component + seat picker**
- New `<Lobby>` component, list of `<TableCard>`s.
- Each `<TableCard>` shows the 4-AI roster (avatars + names +
  relationship hints) + 2 open-seat tap targets (one is the
  live-fill slot, indistinguishable in UI from the intent slot —
  player taps either).
- Replaces the current `<CashModeEntry>` stake picker.
- Existing SponsorModal opens when seat tap rejects with
  `requires_sponsor`.
- TypeScript checked.

## Locked decisions (from design discussion 2026-05-18)

1. **AI movement model** — three triggers, evaluated at hand
   boundaries:
   - **Won big** (table chips ≥ `2× their buy-in`) → roll for
     stake-up. If their bankroll affords the next tier, ~30%
     chance to leave this table and queue for a higher-stake
     seat. Otherwise ~10% chance to "take a break" (move to idle
     pool).
   - **Lost big** (table chips ≤ `0.3× their buy-in`, incl. 0) →
     leave the table, move to idle pool. From idle their projected
     bankroll determines re-entry tier.
   - **Otherwise** → stay put. Small base ~1-2% per hand "boredom
     move" to keep the lobby cycling.

   **Idle pool**: separate `cash_idle_pool` table (schema v92,
   commit 2). Re-entry tick: pick the highest stake their
   projected bankroll affords; 30% chance of dropping one tier
   for variety. Idle duration bounded ~3-10 min wall clock so
   the lobby keeps cycling. Tunable.

   **Where movement is decided**: at any table's hand-boundary,
   evaluate the AIs at *that* table. For tables without a player
   present (no active hands), movement is evaluated lazily at
   each `GET /api/cash/lobby` read — see commit 5. Full Path C's
   live AI-only hand sim replaces this lazy cadence later.

2. **Multi-active-table policy** — ONE table per stake for v1.5.
   The schema admits more without redesign; bump
   `ensure_lobby_seeded` later if the lobby looks too quiet.

3. **In-flight session migration on deploy** — kill them all.
   Boot hook: `kill_all_cash_sessions()` runs once at the deploy
   that lands commit 1, then `ensure_lobby_seeded()` runs as
   normal. Single active user (one developer); no preservation
   needed.

4. **Sponsor modal scope** — narrow to the table's AI roster
   first; fall back to house archetypes only if zero personalities
   at that table qualify as lenders (likability/heat/respect
   gates or active loan already outstanding).

5. **Open seats per table — 2, with live fill.**
   Tables have **6 slots: 4 AI baseline + 2 open**. One open
   seat is the human's intent slot; the other is the "live fill"
   slot. During an active session, the live-fill seat has a
   per-hand probability (~15% — tune in playtest) of an eligible
   AI walking up to sit. The new AI takes the seat between hands
   and plays the next deal normally; their own movement logic
   applies thereafter. **This is the "feel alive" piece** —
   the table changes while you're at it, not just between
   sessions.

   For tables without a player present, live fill happens at
   the lobby read tick (cheap), so the lobby visually cycles
   even when no game is running.

## Why this is the right scope right now

- **Doesn't block on Path C's hard parts** — the AI-only hand
  simulator, statistical catch-up sim, AI-vs-AI lending, and
  rivalry-seek seating are all separate.
- **Captures the immediate UX win**: player sees who's at each
  table, picks a seat, feels like a cardroom.
- **Sets up the data model Path C needs** — persistent tables
  with persisted seats are the durable substrate Path C's
  background sim will tick forward.
- **Roster persistence is the load-bearing piece**: once AIs hold
  chips across sessions, every other Path C feature has somewhere
  to attach (rivalry seeks the table where their preferred player
  last sat; AI bankroll regen meets persistent table state to
  decide stake drift; AI-to-AI loans flow between tables).

## Open questions (won't block start, but flag during implementation)

- **Display name uniqueness in lobby**: if two tables seat the
  same personality (shouldn't happen but the eligibility pool
  could theoretically overlap), how to handle? Recommendation:
  hard rule in `refresh_table_roster` that an AI is on **at most
  one** table at a time. Verify in tests.
- **Avatar URL pipeline**: existing avatar generation might not
  surface URLs for the lobby's read path. Check
  `poker/avatar_generation/` or whatever path the in-game avatar
  loading uses and reuse.
- **Sticky table_id format**: `cash-table-$2-001` is a starter;
  the dollar-sign isn't URL-safe. Use `cash-table-2-001` or a
  random suffix.

## Files to read first

1. **This doc** — design above.
2. **`docs/plans/CASH_MODE_PATH_C_DESIGN.md`** — context for why
   this is the v1.5 subset.
3. **`flask_app/routes/cash_routes.py`** — `_build_cash_game`,
   `start_cash_session`, `leave_table`, `cleanup_orphan_cash_games`.
4. **`poker/repositories/schema_manager.py`** — v89/v90 migration
   patterns to mirror.
5. **`cash_mode/sponsor_offers.py:compute_personality_offers`** —
   the candidate-narrowing site for commit 6.
6. **`react/react/src/components/cash/CashModeEntry.tsx`** — the
   stake picker this lobby replaces.

## What's deferred (still Path C)

- Live AI-only hand simulation while the player is away.
- Catch-up statistical advance on player return.
- AI stake drift (bankroll-growth-triggered table changes).
- Rivalry-seek seating (high-heat AIs relocating to the player's
  table).
- AI-to-AI lending.
- Multi-active-table per stake.
- Spectator mode.
