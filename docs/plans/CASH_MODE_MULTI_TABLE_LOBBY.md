---
purpose: Plan for multi-table-per-tier cash lobby with user-friendly names, mobile views, and prep for private/casino table types
type: design
created: 2026-05-22
last_updated: 2026-05-22
---

# Multi-Table Lobby — Plan

## Goal

Move the cash lobby from "one canonical table per stake tier" to "N named tables per stake tier," with table-type extensibility for future private + casino tables. Update staking so sponsors target a specific table.

## Why now

Bankroll-eligibility data on the user's sandbox shows 84 cash-eligible personalities but only ~30 lobby seats (5 tables × 6 seats). Even with the just-merged randomized seed (`cash_mode/lobby.py:294-302`), the lobby surfaces ≤ 30 personalities at a time. More tables = better roster rotation + room for venue flavor (a $10 dive bar, a $200 hotel suite, a $1000 high-roller pit).

This change also prepares the rails for two near-future features (separate PRs):

1. **Private tables** — a human creates a table, picks the stake, invites others (or AI personalities). Not part of this PR's scope, but the schema/data flow must not preclude it.
2. **Casino tables** — special house-run tables with different rake, sponsor rules, or themed AI lineups. Same: schema only.

## Current State Audit (2026-05-22)

### Backend is mostly already table-id-keyed

Surprising finding from the codebase audit: the backend is **already designed for N tables per stake** — only the boot-time seed enforces N=1.

- **Schema** (`schema_manager.py:4725`): `cash_tables` PK is `(table_id, sandbox_id)`. Nothing in the PK locks you to one row per stake. The comment on `_table_id_for_stake` (`lobby.py:100`) literally says "primary v1.5 lobby table" — the `-001` suffix was reserved for future siblings.
- **`GET /api/cash/lobby`** (`cash_routes.py:3603`): already returns every row from `list_all_tables` — adding a `cash-table-10-002` row appears in the response without any route change.
- **`POST /api/cash/sit`** (`cash_routes.py:1014`): client already sends `{table_id, seat_index}` — explicit, not stake-keyed.
- **`POST /api/cash/sponsor-and-sit`** (`cash_routes.py:1467`): already accepts an optional `table_id` (lines 1508, 1532). The fallback path that lacks `table_id` is the only ambiguity.
- **`refresh_unseated_tables`** (`lobby.py:384`): iterates all tables; already correct.
- **Movement / idle pool / stake-up** (`movement.py`): `target_stake` on `IdlePoolEntry` is tier-keyed, not table-keyed — re-entry picks any open seat at the target tier, which behaves correctly for N tables.
- **Sit/Leave session lifecycle**: settlement is keyed on `session_id = game_id` (`stake_settlement.py`) — no table assumptions.

### Frontend is mostly already table-id-keyed too

- `LobbyTable.table_id` is already in the client type (`types.ts:159`).
- `sitAtTable(table_id, seat_index)` in `api.ts:221` and `sponsorAndSit` (`api.ts:107`) already POST `table_id`.
- `SponsorModal` already receives `origin.tableId` (`SponsorModal.tsx:30`).

The frontend gaps are mostly cosmetic/UX:
- `TableCard.tsx:77` renders `"{stake_label} table"` — no per-table name yet.
- `Lobby.tsx:179-200`'s `handleStakeClick` does `tables.find(t => t.stake_label === ...)` — with multiple tables per tier this returns the first match arbitrarily. Needs to either disable the stake-quickpick or let the user disambiguate.
- The flat auto-fill grid (`CashMode.css:49`) needs grouping by tier so 11 tables don't display as a sea of 11 unrelated cards.

### Real choke points (where the 1-per-stake assumption lives)

| File | Line(s) | What lives there |
|---|---|---|
| `cash_mode/lobby.py` | 100, 280 | `_table_id_for_stake` returns `cash-table-{slug}-001`; `ensure_lobby_seeded` loops `STAKES_ORDER` and seeds one table per stake |
| `cash_mode/tables.py` | 64 | `CashTableState` has no `name` / `table_type` field |
| `schema_manager.py` | 4725 | `cash_tables` DDL has no `name` / `table_type` column |
| `flask_app/routes/cash_routes.py` | 1700-1736 | `sponsor_and_sit` persists `stake_tier=stake_label` to the Stake row, never writes `table_id` |
| `cash_mode/stakes.py` | 104 | `Stake` dataclass has `stake_tier`, no `table_id` |
| `react/.../cash/Lobby.tsx` | 179-255 | Flat grid + `handleStakeClick` assumes 1 table per stake |
| `react/.../cash/TableCard.tsx` | 77 | Renders `"{stake_label} table"` (no name field) |
| `react/.../cash/SponsorModal.tsx` | 136 | Title references stake label only |

### Tests that pin current behavior

Listed for cost-estimation; each hardcodes `cash-table-{slug}-001`:
- `tests/test_cash_sit_route.py` (15+ literals; tests 1:1 invariant directly at line 420)
- `tests/test_cash_lobby_route.py`
- `tests/test_cash_lobby_integration.py`
- `tests/test_cash_sponsor_routes.py`
- `tests/test_cash_mode/test_lobby_seeding.py`
- `tests/test_chip_ledger_audit.py`

These need to either keep using `-001` (still valid as the canonical first table per stake) or be updated to use the new lobby config.

## Proposed Design

### Stake-tier layouts (Day-1 numbers, easy to tune later)

Names are placeholders — meant to feel like "where would you play poker at this stake."

| Stake | Count | Suggested names |
|---|---:|---|
| $2 | 2 | `The Back Room`, `Coffee Counter` |
| $10 | 3 | `Murphy's Bar`, `The Garage`, `Saturday Home Game` |
| $50 | 3 | `Riverside Card Club`, `The Lodge`, `Tuesday Night Reg` |
| $200 | 2 | `Hotel Mezzanine`, `The Quiet Room` |
| $1000 | 1 | `High Roller Pit` |

That's 11 tables, 20→44 baseline AI seats (52% roster utilization). Tunable via a config dict — see "Lobby config" below.

### Schema additions (one migration)

Add two nullable columns to `cash_tables`:

```sql
ALTER TABLE cash_tables ADD COLUMN name TEXT;
ALTER TABLE cash_tables ADD COLUMN table_type TEXT DEFAULT 'lobby' NOT NULL;
-- Optional, for future private tables: ADD COLUMN owner_id TEXT
```

`table_type` enum (string for forward compat): `'lobby'` (current), `'private'` (future), `'casino'` (future). For this PR, every seeded table is `'lobby'`.

`name` is the human-facing label. NULL → frontend falls back to `"{stake_label} table"` (preserves behavior for any tables that exist without a name).

Add `table_id TEXT` column to `stakes` (nullable for back-compat with existing rows):

```sql
ALTER TABLE stakes ADD COLUMN table_id TEXT;
```

Backfill not needed — existing rows are settled; new rows will populate from `sponsor_and_sit`. Per-table analytics (which $50 table earns the house more rake?) come for free once the column is populated.

### Lobby config (single source of truth)

New file `cash_mode/lobby_config.py`:

```python
LOBBY_TABLES: dict[str, list[dict]] = {
    "$2":   [{"id_suffix": "001", "name": "The Back Room"},
             {"id_suffix": "002", "name": "Coffee Counter"}],
    "$10":  [{"id_suffix": "001", "name": "Murphy's Bar"},
             {"id_suffix": "002", "name": "The Garage"},
             {"id_suffix": "003", "name": "Saturday Home Game"}],
    "$50":  [{"id_suffix": "001", "name": "Riverside Card Club"},
             {"id_suffix": "002", "name": "The Lodge"},
             {"id_suffix": "003", "name": "Tuesday Night Reg"}],
    "$200": [{"id_suffix": "001", "name": "Hotel Mezzanine"},
             {"id_suffix": "002", "name": "The Quiet Room"}],
    "$1000":[{"id_suffix": "001", "name": "High Roller Pit"}],
}
```

`ensure_lobby_seeded` iterates this config instead of `STAKES_ORDER` directly. Existing `cash-table-{slug}-001` tables keep their id, just get a name attached. New tables get `-002`, `-003`, etc.

Why a Python dict and not a DB-driven config? Lobby tables are seeded code-side at boot; the dict gives us one obvious place to add/rename and keeps migrations simple. When private tables ship, the DB row itself becomes the source for those (config dict still drives the public lobby).

### Backend changes (per choke point)

1. **`cash_mode/lobby.py:100`** — `_table_id_for_stake(stake_label, suffix="001")` becomes a helper for the config loop. Existing callers stay on default `"001"`.
2. **`cash_mode/lobby.py:280`** — `ensure_lobby_seeded` outer loop changes from `for stake_label in STAKES_ORDER` to a nested loop over `LOBBY_TABLES.items()` → `for cfg in entries`. The randomized-shuffle that just landed keeps working per-table (each new table gets a fresh `seed_rng.shuffle(eligible)`).
3. **`cash_mode/tables.py:64`** — `CashTableState.name: Optional[str]` and `CashTableState.table_type: str = 'lobby'`. Persistence layer reads/writes the new columns; existing seats_json untouched.
4. **`flask_app/routes/cash_routes.py:1700-1736`** — `sponsor_and_sit` always writes `table_id` to the `Stake` row (column added in migration). Settlement reads it if needed for per-table analytics.
5. **`cash_mode/sponsor_offers.py:48`** — sponsor-offer objects gain an optional `table_id` (already supported by the route narrowing logic).
6. **Player-stake AI flow** (`cash_mode/player_staking.py`) — when a human stakes an AI, the UI should let them pick which table; the API gets a `table_id` field. Out of scope for this PR if you'd rather keep player-stake-AI tier-only and just ensure the AI joins *some* table at that tier; mark as a TODO.

### Frontend changes

**Data layer** — `types.ts`:
- Add `table_name: string | null` and `table_type: 'lobby' | 'private' | 'casino'` to `LobbyTable`.
- `sponsorOffer` may carry an optional `table_id` (already conditional on the route).

**Layout** — `Lobby.tsx` switches from flat grid to tier-grouped layout:

```
┌─ $2 — The Back Room • Coffee Counter ──────── 2 tables ─┐
│   [TableCard]  [TableCard]                              │
├─ $10 — Murphy's Bar • The Garage • Saturday  3 tables ─┤
│   [TableCard]  [TableCard]  [TableCard]                 │
...
```

Each tier section has a sticky header (label + count). Below: the actual cards.

**Mobile UX** — recommendation:

Default to a **vertical list with collapsed-by-default tiers** for mobile, expanded-by-default for desktop. Tap a tier header to expand. Reasoning:

- A poker lobby has a small, fixed number of tiers — collapsibility is a "show only what I'm playing" affordance, not a discovery aid.
- Tables within a tier are comparable peers — list view lets the user scan AI rosters side-by-side without horizontal interaction.
- A carousel adds polish but hides tables behind a swipe. Discoverability cost > screen-real-estate win at 2-3 tables per tier.

**If you want carousels:** make it the desktop-only flourish — large screen has room for a single-row horizontal carousel per tier, mobile gets the list. CSS-only via `scroll-snap-type` + `overflow-x: auto` — no JS carousel library.

**Sponsor modal** — `SponsorModal.tsx:136` shows table name + stake when both known: `"Sponsor for The Garage ($10)"`.

**Stake-card quickpick** — `Lobby.tsx:179-200`'s `handleStakeClick`: with multiple tables per tier, this either:
- (a) opens a "pick a table" sub-modal, or
- (b) becomes obsolete (force the user to tap a specific table card).

Recommend (b) for simplicity. The tier header becomes informational, not a click target. The card itself remains the unit of action.

### Future-proofing (no code in this PR)

- `table_type='private'` row + `owner_id` column → private tables. Seeding skips them; movement system filters them out (already filterable by `table_type` once the column exists).
- `table_type='casino'` row → house-themed tables. Settlement / sponsor rules can branch on `table.table_type` at the choke points identified in the audit (`sponsor_offers.compute_personality_offers`, `stake_settlement._compute_chip_flows`).
- Both future types just need a column-aware filter on `list_all_tables` for the lobby endpoint (e.g., `list_lobby_tables(sandbox_id)` filters `table_type='lobby'`; private tables get their own endpoint).

## Implementation Sequence (suggested)

Five small PRs, each independently shippable:

1. **Schema + dataclass** — migration adding `name`, `table_type` to `cash_tables` and `table_id` to `stakes`. `CashTableState` gains the fields. Repos read/write them. No behavior change, no UI change. (~150 LOC, ~10 test updates)

2. **Lobby config + seed expansion** — `lobby_config.py`, `ensure_lobby_seeded` reads it, new tables get seeded on boot. Hardcoded test ids stay valid (`-001` is preserved). New tests for the 2nd+ table case. (~200 LOC)

3. **Frontend tier grouping** — `LobbyTable` gains `table_name`/`table_type`; `Lobby.tsx` switches to grouped layout; `TableCard.tsx` renders the name; `SponsorModal` includes it. Pure UI change. (~300 LOC)

4. **Mobile collapsibility** — list/expanded view toggle, CSS-only carousel as optional polish.

5. **Staking table-id binding** — `sponsor_and_sit` always writes `table_id` to the Stake row; player-stake-AI flow gets `table_id` in the API and UI. Settlement/analytics get the column populated.

## Open Questions

These don't block planning but should be answered before implementation:

1. **Table-count tuning** — the 2/3/3/2/1 split is a guess. Worth checking against actual lobby seat-utilization data after a few days of randomized seeding. *Recommendation:* ship the proposed counts; revisit after a week of telemetry.
2. **Name source** — hardcoded dict vs. DB-editable via admin UI? *Recommendation:* dict now (zero infra cost), admin-UI later if/when the names get refreshed often.
3. **Carousel on desktop** — yes or no? *Recommendation:* no for now; revisit once we see how the grouped list looks with the actual counts.
4. **Stake-tier card quickpick** (Lobby.tsx:179-200) — kill it, or expand into a "pick a table" dialog? *Recommendation:* kill it; the tier header becomes informational.
5. **Player-stake-AI table targeting** — does staking UI need to ask which table, or is "any table at the tier" fine? *Recommendation:* "any table at the tier" for v1 — write a TODO; revisit when player-stake-AI usage data shows the ambiguity matters.
6. **Backfill old `cash-table-{slug}-001` rows with a name?** *Recommendation:* yes, in migration — set name from the config dict's `-001` entry so existing tables don't render with a fallback string.
7. **Sandbox-scoped naming?** Right now `name` is global per-table. Private tables might want per-sandbox names. *Recommendation:* `name` is per-row (which is per-`(table_id, sandbox_id)`), so it's already sandbox-scoped at the storage layer. No change needed.

## Out of Scope

- Actual private-table creation flow (UI, API, ownership semantics) — schema only.
- Casino-table specifics (rake formulas, AI lineup curation) — schema only.
- Admin UI for renaming lobby tables — dict-edit + redeploy for v1.
- Per-table analytics dashboards — column is populated, dashboards come later.
- Movement-system table preferences (e.g., AI prefers The Garage over Murphy's Bar based on relationship) — explicitly punted.

## Risk Notes

- **Test churn**: ~6 test files hardcode `cash-table-{slug}-001`. Most assertions are still valid (the `-001` table still exists by that id). Tests that assert *only* `-001` exists in the lobby (e.g., `len(lobby_tables) == 5`) need updating.
- **Performance**: 5 → 11 tables triples the per-lobby-GET workload (movement rolls, live-fill rolls). Today the cadence is "lazy" (every lobby GET). At 11 tables × every-8-second poll × multiple concurrent sandboxes, monitor `refresh_unseated_tables` latency. Mitigation: throttle the refresh per-table (only run movement on a given table at most every N seconds, regardless of how often the lobby is fetched).
- **Sandbox seeding race**: `ensure_lobby_seeded` is idempotent today; adding tables won't change that. But re-runs against an *existing* sandbox need to add missing tables without disturbing existing ones. The dedup-by-table-id logic at `lobby.py:283` already handles this.
- **Frontend polling cost**: lobby payload grows ~2.2x. Verify no obvious regressions in the FloatingChat/ActivityTicker hot paths.

## Files I Need to Touch (estimate)

Backend:
- `cash_mode/lobby.py` (seed loop, `_table_id_for_stake`)
- `cash_mode/lobby_config.py` (new)
- `cash_mode/tables.py` (`CashTableState` fields)
- `cash_mode/stakes.py` (`Stake.table_id` optional field)
- `poker/repositories/cash_table_repository.py` (read/write new columns)
- `poker/repositories/stake_repository.py` (table_id passthrough)
- `poker/repositories/schema_manager.py` (new migration vN)
- `flask_app/routes/cash_routes.py` (sponsor_and_sit writes table_id)
- `cash_mode/sponsor_offers.py` (optional table_id in offer objects — may be no-op)

Frontend:
- `react/react/src/components/cash/types.ts`
- `react/react/src/components/cash/Lobby.tsx`
- `react/react/src/components/cash/TableCard.tsx`
- `react/react/src/components/cash/SponsorModal.tsx`
- `react/react/src/components/cash/CashMode.css`
- `react/react/src/components/cash/api.ts` (likely no changes — already table-id-keyed)

Tests:
- Updates to ~6 test files; new tests covering the multi-table seed path, the per-table `Stake.table_id` field, and the frontend tier-grouped layout.

## Decision Points Needed Before Implementation Starts

The "Open Questions" section above lists 7 open items. Five have recommendations baked in; the two that most affect scope are:

- **(1) Table counts** — the 2/3/3/2/1 split. If you want a different split, edit `LOBBY_TABLES`.
- **(5) Player-stake-AI table targeting** — punt or implement now? Affects PR #5 size.

Everything else can be decided during implementation without changing the plan shape.
