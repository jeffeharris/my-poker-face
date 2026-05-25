---
purpose: Rework the admin Chip Economy "Player Holdings" section into a per-entity net-worth view (table + over-time chart) backed by recorded snapshots
type: design
created: 2026-05-25
last_updated: 2026-05-25
---

# Cash Mode — Net Worth Holdings View

> **Status: Implemented (2026-05-25).** Schema v116 + recorder + chart/table
> shipped; backend smoke-tested against live data, 15 new unit tests +
> regression suites green, TypeScript clean.

## Problem

The admin **Chip Economy → Player Holdings** section is wrong in two ways:

1. **The line chart plots the wrong quantity.** It is titled/read as "player
   holdings over time" but actually plots *cumulative net chips received from
   the central bank* (`compute_holdings_history` walks `chip_ledger_entries`).
   That diverges from — and for net winners **inverts** — actual holdings,
   because chips won/lost seat-to-seat never touch the ledger, and a winner's
   excess swept to the bank pool (`bank_pool_deposit`) reads as chips *leaving*
   them. Observed: `someone_who_is_very_very_mean_to_people` charts **−124k**
   while its real bankroll is **+221k**; `cleopatra` charts **+195k** vs a real
   **40k**.

2. **The table shows incomplete/misleading data.** The `Won / Lost / Net`
   columns come from `cash_pair_stats`, which massively under-records actual
   play (86 pair rows vs 37k+ rake events), so they look "completely wrong"
   next to six-figure bankrolls. There is no visibility into vice spending or
   side-hustle earnings, which the economy now tracks per entity.

## Goal

Turn the section into a **net worth** view.

```
net worth = liquid chips + stakes receivable − stakes outstanding
```

- **Chart**: net worth *over time*, per entity.
- **Table**: per-entity breakdown — net worth, chips, stakes receivable,
  stakes outstanding, vice spent, side-hustle earned.

## Locked decisions (from review)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Multi-sandbox attribution | **Net worth only when a sandbox is selected.** The cross-sandbox ("All sandboxes") net-worth view is deprecated — it isn't useful. In All-sandboxes view the table shows chips only; the chart asks the admin to pick a sandbox. |
| 2 | Snapshot cadence | **Time-based, ~10 min per active sandbox**, retain 30 days. (A hand-count / game-tick trigger is noted as a future refinement but not built now.) |
| 3 | Won/Lost/Net columns | **Dropped.** Remove the `cash_pair_stats` PnL columns and the `aggregate_cash_pnl_by_entity` call from this view. |
| 4 | Filtering/analytics scope | **Minimal.** Fix the data + add columns. The only chart-behavior change is **auto-fitting the time window to available data** (correctness, so a young economy isn't a 97%-flat line) — not a new filter. |

## Net worth definition (per entity, within a sandbox)

- **liquid chips**
  - AI: `bankroll_repo.load_ai_bankroll_current(pid, sandbox_id, now)` (projected through regen — matches what a live read returns).
  - Human: `player_bankroll_state.chips` (humans are global, not sandbox-scoped in v1).
- **stakes receivable** (entity is the staker/creditor) — **global** (`stakes` has no `sandbox_id`):
  - active stakes: `SUM(principal + match_amount)` where `staker_id = entity AND status='active'`
  - carry receivables: `SUM(carry_amount)` where `staker_id = entity AND status='carry'`
- **stakes outstanding** (entity is the borrower/debtor) — **global**:
  - `SUM(carry_amount)` where `borrower_id = entity AND status='carry'`
- **net worth** = liquid chips + receivable − outstanding

Mirrors the existing per-entity calc at `GET /api/cash/net-worth`
(`flask_app/routes/cash_routes.py`).

### Known caveat (accepted)

Stakes are global per entity; chips are per-sandbox. Because net worth is only
shown in the single-sandbox view, an entity active in two sandboxes would see
its global stake totals attributed to whichever sandbox is being viewed. This
is acceptable given decision #1 (you only ever view one sandbox at a time, and
the global view is deprecated). A future refinement could scope stakes by
joining `stakes.session_id → games.sandbox_id`; out of scope here.

Entity-id matching is clean: `ai_bankroll_state.personality_id`,
`cash_pair_stats.observer_id`, and `stakes.staker_id/borrower_id` all use the
bare slug (e.g. `don_quixote`); the chip ledger and these views key on
`ai:<slug>` / `player:<id>`.

## Architecture

### Data flow (target)

```
                          background world ticker (_tick_sandbox)
                                     │  every ~10 min / sandbox
                                     ▼
   compute net worth per entity ─► holdings_snapshots (new table, v116)
   (chips + stakes recv/owed)              │
                                            ▼
   GET /holdings/history  ──► HoldingsSnapshotsRepository.series_since()
                                            │  net worth over time, per entity
                                            ▼
                                    HoldingsChart (relabeled "Net worth")

   GET /holdings  ──► compute_holdings_snapshot (now: net worth + components,
                       vice/side-hustle from ledger, NO cash_pair_stats PnL)
                                            ▼
                                    HoldingsTable (new columns)
```

### Backend changes

**1. `poker/repositories/schema_manager.py` — migration v116**
- Bump `SCHEMA_VERSION` 115 → 116; register `_migrate_v116_create_holdings_snapshots` (template: `_migrate_v115_create_user_preferences`).
- New table:
  ```sql
  CREATE TABLE IF NOT EXISTS holdings_snapshots (
      snapshot_id  INTEGER PRIMARY KEY AUTOINCREMENT,
      captured_at  TIMESTAMP NOT NULL,        -- explicit ISO-8601 UTC, written by recorder
      sandbox_id   TEXT NOT NULL,
      entity_id    TEXT NOT NULL,             -- 'ai:<slug>' | 'player:<id>'
      kind         TEXT NOT NULL,             -- 'ai' | 'player'
      net_worth    INTEGER NOT NULL,
      chips        INTEGER NOT NULL,
      receivable   INTEGER NOT NULL DEFAULT 0,
      outstanding  INTEGER NOT NULL DEFAULT 0
  );
  CREATE INDEX idx_holdings_snap_scope ON holdings_snapshots(sandbox_id, captured_at);
  CREATE INDEX idx_holdings_snap_entity ON holdings_snapshots(sandbox_id, entity_id, captured_at);
  ```
  Components (chips/receivable/outstanding) are stored alongside net_worth so the
  curve is explainable and future metric toggles are cheap. `captured_at` is
  written as explicit `YYYY-MM-DDTHH:MM:SS...Z` so reads never hit the
  space-vs-`T` lexical-compare bug (already fixed once in `holdings_view`).

**2. `poker/repositories/stake_repository.py` — bulk aggregates (avoid N+1)**
- `aggregate_receivables_by_staker() -> dict[str,int]`
  ```sql
  SELECT staker_id,
         SUM(CASE WHEN status='active' THEN principal+match_amount ELSE 0 END)
       + SUM(CASE WHEN status='carry'  THEN carry_amount          ELSE 0 END) AS receivable
  FROM stakes WHERE staker_id IS NOT NULL GROUP BY staker_id
  ```
- `aggregate_outstanding_by_borrower() -> dict[str,int]`
  ```sql
  SELECT borrower_id, SUM(carry_amount) AS outstanding
  FROM stakes WHERE status='carry' GROUP BY borrower_id
  ```

**3. `poker/repositories/holdings_snapshots_repository.py` — new repo**
- `record(rows: list[dict], *, captured_at: str) -> int` — bulk insert one pass.
- `series_since(since_iso, *, sandbox_id, max_points_per_entity) -> dict` — per-entity ordered points (net_worth + components), oldest first.
- `latest_captured_at(sandbox_id) -> str | None` — for the recorder's rate-limit / seed check.
- `prune(older_than_iso) -> int` — 30-day retention.
- Register in `poker/repositories/__init__.py::create_repos` and wire the global in `flask_app/extensions.py::init_persistence`.

**4. `flask_app/services/holdings_view.py`**
- `compute_holdings_snapshot`:
  - Drop the `cash_pair_stats` PnL path (`aggregate_cash_pnl_by_entity`, `chips_won/lost/net_pnl`).
  - Add per-entity **vice_spent** (`reason='vice_spending'`, entity = source) and **side_hustle_earned** (`reason='side_hustle_earning'`, entity = sink) aggregated from `chip_ledger_entries`, scoped by sandbox.
  - When `sandbox_id` is set: add `receivable`, `outstanding`, `net_worth` from the stake aggregates. When `None`: omit them (chips-only rows) and set a payload flag `net_worth_scoped=False`.
- New `record_holdings_snapshot(*, sandbox_id, repos, now)`: reuses the scoped snapshot computation and writes rows via the snapshots repo. (Recorder lives here or in a thin `snapshot_recorder.py`.)
- `compute_holdings_history`: **repoint** from the ledger walk to `snapshots_repo.series_since(...)`. Series value = `net_worth`. Compute `effective_since = max(window_start, earliest_point)` so the x-domain fits the data (kills the flatline). When `sandbox_id is None`, return empty series + `requires_sandbox=True`.

**5. `flask_app/services/ticker_service.py`**
- In `_tick_sandbox`, after `refresh_unseated_tables(...)` and before `lobby_tick` emit: call the recorder, **rate-limited** via module-level `_last_snapshot_at[sandbox_id]` (default 600s). Wrap in try/except (the cycle already swallows, but keep snapshotting from ever delaying a tick). Also opportunistically **seed** in the history endpoint if no snapshot exists yet, so the chart isn't empty before the first tick fires.

**6. `flask_app/routes/chip_ledger_routes.py`**
- Pass `stake_repo` + `holdings_snapshots_repo` into the holdings calls. No new routes — same two endpoints, new payload shape.

### Frontend changes — `react/react/src/components/admin/ChipLedgerPanel.tsx`

- **Types**: `HoldingsRow` drops `chips_won/chips_lost/net_pnl`; adds `net_worth`, `receivable`, `outstanding`, `vice_spent`, `side_hustle_earned`. History point value is net worth.
- **Table** (`HoldingsTable`): columns become **Player · Kind · Net worth · Chips · Recv · Owed · Vice · Side hustle · Sandbox**. Sort keys updated; default sort `net_worth desc`. In All-sandboxes view, render only Player/Kind/Chips/Sandbox with an inline note "Select a sandbox to see net worth."
- **Chart** (`HoldingsChart`): relabel to **"Net worth over time"**; plot net worth (already handles negative ranges); update the caveat text; when `requires_sandbox`, show "Select a sandbox to chart net worth." Window auto-fits via backend `since`.
- Remove the old "net chips received from the central bank" caveat copy.

## Testing

- `tests/test_holdings_view.py`: drop PnL assertions; add net-worth/vice/side-hustle assertions; assert All-sandboxes omits net worth; assert history reads snapshots + auto-fit `since`.
- New `tests/test_holdings_snapshots_repository.py`: record/series/prune round-trip; ordering; per-sandbox isolation.
- `tests/test_stake_repository.py` (or existing): bulk receivable/outstanding aggregates incl. active+carry split and `staker_id IS NULL` (house) exclusion.
- Ticker: a focused test that the recorder is rate-limited and never raises into the cycle.
- `python3 scripts/test.py --ts` for the React type changes.

## Migration / rollout notes

- v116 is **non-destructive** (new table only). No backfill of historical net
  worth is possible (the data was never recorded); the curve accrues forward
  from first tick. An initial seed point is written on first view so the chart
  isn't blank.
- The old ledger-walk history code is removed (not kept behind a flag) since
  decision #1 makes the bank-flow curve a non-goal here; the bank-flow data
  still lives in the untouched "Bank pool flow" / "By reason" cards.

## Out of scope

- Reconstructing realized seat-to-seat P&L (not in the ledger).
- Hand-count / game-tick snapshot triggers (time-based only for now).
- Sandbox-scoping the stakes table.
- Net worth in the player-facing UI (admin Chip Economy only).

## File touch list

| File | Change |
|------|--------|
| `poker/repositories/schema_manager.py` | v116 migration + table |
| `poker/repositories/holdings_snapshots_repository.py` | **new** repo |
| `poker/repositories/stake_repository.py` | 2 bulk aggregate methods |
| `poker/repositories/__init__.py` | register snapshots repo |
| `flask_app/extensions.py` | wire snapshots repo global |
| `flask_app/services/holdings_view.py` | net-worth snapshot + recorder + history repoint |
| `flask_app/services/ticker_service.py` | rate-limited recorder hook |
| `flask_app/routes/chip_ledger_routes.py` | pass new repos through |
| `react/.../admin/ChipLedgerPanel.tsx` | table columns + chart relabel + scoped gating |
| `tests/test_holdings_view.py` + new test files | coverage |
