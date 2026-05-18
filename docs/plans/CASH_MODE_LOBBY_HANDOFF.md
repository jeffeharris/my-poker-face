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
- 5 seated AIs (avatars + names, possibly with a small icon if
  there's an outstanding loan between you and them)
- 1 open seat (the player's slot)
- Player's affordability state for this table (afford / sponsor-
  needed / locked) — same tri-state from current entry screen

Tap an open seat → if affordable, sit straight down; if sponsor-
required, open the existing SponsorModal (now with the table's
specific personality lenders eligible).

The current "stake card" UI is the seed; this is essentially
the same component with a roster strip below the meta line.

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

`seats_json` is an array of 6 slot entries (5 AI + 1 human):

```jsonc
[
  {"kind": "human", "personality_id": null, "chips": null},   // open seat
  {"kind": "ai", "personality_id": "napoleon", "chips": 1240},
  {"kind": "ai", "personality_id": "warren_buffett", "chips": 870},
  {"kind": "ai", "personality_id": "donald_trump", "chips": 540},
  {"kind": "ai", "personality_id": "jay_gatsby", "chips": 1810},
  {"kind": "ai", "personality_id": "fred_durst", "chips": 410}
]
```

**Per-seat chips are persisted.** When the player sits, those
values become the AIs' starting stacks at that game's hand 1.
When the player leaves, the game's ending stacks are written back.
**This is the key state coupling**: an AI who wins big on you
keeps those chips for the next player who sits down.

### 2. Lobby maintenance

A boot-time routine (`ensure_lobby_seeded`) that:
- Creates one table per stake (`$2`, `$10`, `$50`, `$200`, `$1000`)
  if missing.
- Fills each table's 5 AI seats with eligible personalities (per
  the existing `list_eligible_for_cash_mode` + per-personality
  affordability check).
- Sets each AI's `chips` to their per-personality buy-in (same
  formula as `_build_cash_game` uses today).

Called from the same startup hook as `cleanup_orphan_cash_games`
(in cash_routes.py — or where it's wired in app init now).

A post-session **roster refresh** routine (`refresh_table_roster`)
runs at `/api/cash/leave`:
- Writes the game's ending chip counts back to `seats_json`.
- For any AI seat where stack ≤ `min_buy_in`, replace with a
  fresh eligible AI from the pool (not currently seated anywhere)
  and reset their chips to their buy-in.
- Bump `last_activity_at`.

**This is the "AI movement" model for v1.5**: AIs move tables
only when busted; they hold their seat otherwise. Full Path C
adds bankroll-driven stake drift, rivalry seek, and bust-without-
player-present.

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

## Suggested commit breakdown (~7 commits)

**Commit 1: Schema v91 — cash_tables**
- ALTER pattern (idempotent CREATE TABLE IF NOT EXISTS).
- New repo: `poker/repositories/cash_table_repository.py` with
  `load_table`, `list_all_tables`, `save_table`.
- `CashTableState` dataclass in `cash_mode/tables.py` (seats are
  a list of typed slot dicts).
- Tests: schema round-trip, JSON seat serialization, defaults.

**Commit 2: Lobby seeding + roster refresh helpers**
- `ensure_lobby_seeded()` — idempotent: creates 5 tables (one per
  stake) if absent, fills seats with eligible AIs at buy-in chips.
- `refresh_table_roster(table_id, game_state)` — writes back end-of-
  session chip counts, replaces sub-min seats with fresh AIs.
- Pure-function tests for the refresh logic (no Flask needed).

**Commit 3: Boot hook — seed lobby on startup**
- Wire `ensure_lobby_seeded()` into the same init path as
  `cleanup_orphan_cash_games`. Backend restart confirms lobby
  exists.
- Manual smoke: `python3 scripts/dbq.py "SELECT * FROM cash_tables"`
  should show 5 rows after first boot.

**Commit 4: GET /api/cash/lobby**
- Reads all tables + player bankroll + computes affordability +
  attaches relationship hints per AI seat.
- Pure-ish — no game state, no socket events.
- Tests: tempdb with seeded lobby, asserts shape + affordability
  tri-state + relationship hint pass-through.

**Commit 5: POST /api/cash/sit (replaces /api/cash/start)**
- Validation: table exists, seat open, affordability check, no
  active session.
- Reuses `_build_cash_game` but with the table's persisted AI
  roster instead of fresh selection.
- Persists seat occupancy immediately.
- Tests: happy path, double-sit rejection, unaffordable rejection
  (→ 402 with `requires_sponsor: true` and lender preview).

**Commit 6: /api/cash/leave roster refresh + sponsor offers
narrowed to table**
- Hook `refresh_table_roster` after settlement.
- Update `compute_personality_offers` (or its route caller) to
  scope candidates to the table's AI roster.
- Tests: leave + re-read lobby shows updated chip counts.

**Commit 7: Frontend — lobby component + seat picker**
- New `<Lobby>` component, list of `<TableCard>`s.
- Each `<TableCard>` shows roster (avatars + names + relationship
  hints) + open-seat tap target.
- Replaces the current `<CashModeEntry>` stake picker.
- Existing SponsorModal opens when seat tap rejects with
  `requires_sponsor`.
- TypeScript checked.

## Decisions needed before starting

1. **AI movement between sessions.** v1.5 default: AIs only move
   tables when busted. If the user wants AIs to drift between
   tables based on bankroll growth (Bezos at $2 wins enough to
   shop up to $10), call that out — it's a small extra rule in
   `refresh_table_roster`.

2. **Multi-active-table policy.** v1.5 default: ONE table per
   stake. If you want 2-3 tables per stake so the lobby looks
   busier, the schema supports it; just adjust `ensure_lobby_seeded`.

3. **What about pre-existing cash sessions when this ships?**
   Recommendation: tear down any in-flight cash session on
   deploy (the migration is breaking — `/api/cash/start` is gone,
   so already-seated players need to be re-routed to the new
   `/api/cash/sit`). Wire a one-shot cleanup that calls
   `leave_table` for every active cash session at boot, then
   seeds the lobby.

4. **Sponsor modal scope** — should sponsor offers ALWAYS narrow
   to table-roster, or only when there are eligible personality
   lenders there (else fall back to wider pool)? Doc above
   defaults to "narrow first, fall back to house" — confirm.

5. **Open seat per table.** Should there be ONE open seat per
   table (current design), or N open seats (for future multi-
   human seating)? v1.5 = one, since cash mode is single-player
   facing. Locking this in until full Path C if uncontested.

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
