---
purpose: Phase 2.5 — scope cash mode's shared AI economy to per-player sandboxes so each player has their own bankrolls, lobby tables, idle pool, and activity history.
type: guide
created: 2026-05-20
last_updated: 2026-05-20
---

# Cash Mode — Per-Player Sandbox Handoff (Phase 2.5)

> **Read first:**
> - [`CASH_MODE_BACKING_SYSTEM_HANDOFF.md`](CASH_MODE_BACKING_SYSTEM_HANDOFF.md)
>   — locks the stake-model design and lays out Phases 1-5. Phase 2.5
>   slots between the completed Phase 2 and Phase 4 (AIs as borrowers).
> - [`CASH_MODE_ECONOMY.md`](../technical/CASH_MODE_ECONOMY.md) — the
>   chip-bearing surfaces this work scopes. Pools, flow paths, audit
>   model.
> - [`CASH_MODE_LOBBY_HANDOFF.md`](CASH_MODE_LOBBY_HANDOFF.md) —
>   `cash_tables` + `cash_idle_pool` are the lobby surfaces; per-player
>   sandboxing changes their key shape.

> **Why this slots before Phase 4:**
> Phase 4 wires AIs as borrowers, with personality-stakes-personality
> events fired into a shared activity ticker. That assumes a global
> economy: Napoleon staking Bezos in Player A's session is meaningful
> drama for Player B to watch. Per-player sandboxes break that
> assumption — each player only sees their own Napoleon staking
> their own Bezos. Building Phase 4 against the shared model and
> then retrofitting per-player is much more work than doing the
> scoping first. The Phase 4 design's spec text doesn't change
> meaningfully; the implementation just picks up `owner_id` as
> another parameter and the activity ticker becomes per-bucket.

## What's already per-player vs shared

| Surface | Status | Why it matters |
|---|---|---|
| `player_bankroll_state` | Per-player (keyed on `player_id`) | ✓ no change needed |
| `stakes` | Per-borrower (keyed on `borrower_id`) | ✓ borrower side fine; staker side gets owner_id this phase |
| `relationship_states` | Per-pair (`observer_id`, `opponent_id`) | ✓ already the model to copy |
| `games` table (cash sessions) | Per-`owner_id` | ✓ |
| `personalities` (definitions) | Per-`owner_id` + visibility | ✓ public personalities still shared as definitions |
| `ai_bankroll_state` | **Shared** by `personality_id` only | One Napoleon, one bankroll across all players. The biggest sticking point. |
| `ai_bankroll_state.emotional_state_json` | **Shared** | Napoleon's tilt is global — Player A's bad beat affects Player B's Napoleon |
| `cash_tables` | **Shared** (5 global tables) | All players see the same lobby rosters |
| `cash_idle_pool` | **Shared** | AIs between sessions globally tracked |
| `chip_ledger_entries` | **Global stream** | source/sink encoding is `ai:<pid>` — doesn't carry owner_id |
| Activity ticker (`cash_mode/activity.py`) | **Shared** (in-memory ring) | "Bezos staked Napoleon" event is global |

## Design — per-player Napoleon

Each player gets their own copy of every AI personality's *runtime
state*: bankroll, idle status, emotional state, table assignment.
The personality *definition* (traits, play style, lender profile in
config_json) stays shared via the existing `personalities` table.

The mental model: like single-player save files in a video game.
Player A's Napoleon is a separate save-state from Player B's
Napoleon — same character template, different bankroll histories,
different emotional baggage, different memories of you.

This is intentionally NOT a multi-tenant database split. We stay in
one SQLite file, one schema, with `owner_id` columns scoping the
runtime surfaces. Simpler ops, simpler audit (admins can still
aggregate across owners), and the relationship layer's already
per-pair-keyed so the surfaces compose naturally.

## Schema scope (v99)

Add `owner_id TEXT NOT NULL` to:

```sql
-- v99: per-player AI state.
ALTER TABLE ai_bankroll_state ADD COLUMN owner_id TEXT NOT NULL DEFAULT '_legacy';
ALTER TABLE cash_tables       ADD COLUMN owner_id TEXT NOT NULL DEFAULT '_legacy';
ALTER TABLE cash_idle_pool    ADD COLUMN owner_id TEXT NOT NULL DEFAULT '_legacy';

-- PKs become composite.
-- SQLite can't ALTER PRIMARY KEY directly — recreate each table with
-- the new PK, INSERT INTO new SELECT FROM old, DROP old, RENAME new.

-- chip_ledger_entries gets owner_id too — but as a *context* column,
-- not part of the source/sink encoding. The 'ai:<pid>' string stays
-- unchanged for back-compat with existing rows; owner_id is the new
-- query dimension.
ALTER TABLE chip_ledger_entries ADD COLUMN owner_id TEXT;

-- New indexes for per-owner queries.
CREATE INDEX idx_ai_bankroll_owner    ON ai_bankroll_state(owner_id);
CREATE INDEX idx_cash_tables_owner    ON cash_tables(owner_id);
CREATE INDEX idx_cash_idle_owner      ON cash_idle_pool(owner_id);
CREATE INDEX idx_chip_ledger_owner    ON chip_ledger_entries(owner_id);
```

**The `_legacy` synthetic owner** captures the pre-migration state.
Existing AI bankrolls all migrate under one `_legacy` owner so the
audit's chip totals stay invariant. Player-first-access seeds a
fresh per-owner AI bankroll set from scratch (or duplicates from
`_legacy` — see "Migration semantics" below).

### Migration semantics — three options for backfill

1. **Synthetic `_legacy` owner, fresh per-player seeds on first access.**
   Existing AI bankrolls retained under `_legacy`; queries by other
   owners return empty until first-access. Cleanest from a chip-
   conservation standpoint. Players migrating to per-player land in
   a fresh universe. **Recommended.**

2. **Duplicate AI bankrolls per existing player.** For every row in
   `player_bankroll_state`, copy the legacy AI bankroll set under
   that player's `owner_id`. Doubles the AI chip universe immediately;
   the audit's drift jumps by `(N players - 1) × sum(ai_bankrolls)`.
   Requires a paired `pre_ledger_universe` reseed to keep drift at 0.
   Not recommended for pre-launch — too much chip-conservation noise.

3. **Drop and reseed.** Truncate `ai_bankroll_state` /
   `cash_table_repo` / `cash_idle_pool` entirely; seed on first
   access. Loses any existing AI bankroll growth (regen-accumulated
   chips), but the simplest from a code standpoint. Acceptable
   pre-launch where there's no real production data.

The handoff defaults to option 1. Option 3 is acceptable if option
1's backfill bookkeeping turns out gnarlier than expected.

## Commit breakdown (~5 commits)

### Commit 1: Schema v99 + repository signatures

- v99 migration: add `owner_id` columns + indexes as above. PK
  recreation done via the standard SQLite table-rebuild pattern (used
  by v27 `_migrate_v27_fix_opponent_models_constraint`).
- Backfill: every existing row gets `owner_id='_legacy'`.
- Update repository method signatures to take `owner_id` as a kwarg.
  Optional during this commit (default to `'_legacy'`) so existing
  callers compile. Subsequent commits drop the default.
- Methods affected:
  - `BankrollRepository.save_ai_bankroll(state, *, owner_id)`
  - `BankrollRepository.load_ai_bankroll(personality_id, *, owner_id)`
  - `BankrollRepository.load_ai_bankroll_current(personality_id, *, owner_id, now=None)`
  - `BankrollRepository.iter_personality_ids_with_bankrolls(owner_id=None)` — when owner_id is None, iterates ALL rows (used by admin audit).
  - `BankrollRepository.sum_ai_bankroll_chips_stored(owner_id=None)`
  - `BankrollRepository.save_emotional_state_json(personality_id, blob, *, owner_id)`
  - `BankrollRepository.load_emotional_state_json(personality_id, *, owner_id)`
  - `BankrollRepository.load_emotional_state_json_for_pids(pids, *, owner_id)`
  - `CashTableRepository.list_all_tables(owner_id=None)` — None for admin
  - `CashTableRepository.load_table(table_id, *, owner_id)` — table_id alone isn't unique anymore
  - `CashTableRepository.save_table(state, *, owner_id, now=None)`
  - `CashTableRepository.save_idle(entry, *, owner_id)`
  - `CashTableRepository.load_idle(personality_id, *, owner_id)`
  - `CashTableRepository.list_idle(owner_id=None)`
  - `CashTableRepository.delete_idle(personality_id, *, owner_id)`
- Tests: schema round-trip + back-compat (legacy default) + per-owner
  isolation (writing under one owner doesn't surface under another).

### Commit 2: Wire owner_id through cash_mode pure helpers

- `cash_mode/lobby.py`:
  - `ensure_lobby_seeded(*, owner_id, ...)` — required kwarg. Each
    player gets their own seed pass.
  - `refresh_unseated_tables(*, owner_id, ...)` — scopes the refresh.
  - `_table_id_for_stake(stake_label, owner_id)` — table IDs become
    `cash-table-<stake>-<owner_id_slug>-001` so the (table_id,
    owner_id) PK doesn't need a uniqueness conflict.
- `cash_mode/movement.py`:
  - `refresh_table_roster(*, owner_id, ...)` — bankroll lookups pass
    owner_id through.
- `cash_mode/sponsor_offers.py`:
  - `compute_personality_offers(*, owner_id, ...)` — bankroll loads
    use it. The `candidate_personalities` list passed in already comes
    from the player's eligible pool (`personality_repo.
    list_eligible_for_cash_mode(user_id=owner_id)`).
- `cash_mode/full_sim.py`:
  - `play_one_hand(*, owner_id, ...)` — sim chips come from per-owner
    bankrolls. AI psychology persistence goes per-owner too.
- `cash_mode/staking_tier.py`:
  - No change (already scoped by borrower_id which IS the owner).
- `cash_mode/stake_chip_flow.py`:
  - No change (operates on a Stake object that doesn't need to know
    about owner scoping — the route layer handles that).

### Commit 3: Wire owner_id through routes + handlers

- `flask_app/routes/cash_routes.py`: every place that calls
  `bankroll_repo.load_ai_bankroll_*`, `cash_table_repo.*`, or
  `cash_mode.lobby.*` needs owner_id (always resolvable from
  `_resolve_owner_id()`).
- `flask_app/handlers/game_handler.py`: hand-boundary hooks that
  refresh tables / project bankrolls.
- Boot hook (`ensure_lobby_seeded` at app startup) **removes**. The
  first-access pattern is the new model — first `/api/cash/lobby` or
  `/api/cash/state` per owner seeds their lobby lazily.
- `flask_app/services/chip_ledger_audit.py`:
  - `compute_audit(*, owner_id=None, ...)` — `None` means
    "aggregate across all owners" (admin endpoint).
  - When `owner_id` is provided, every sum query filters: `WHERE
    owner_id = ?`.
  - Per-player audit endpoint shape unchanged; just scoped.
- `cash_mode/activity.py`:
  - Replace the global ring buffer with `_buckets: Dict[str, deque]`
    keyed on `owner_id`.
  - `recent_events(owner_id, limit=5)`.
  - `record_event(owner_id, ...)`.
- Tests: every fixture that seeds AI bankrolls now passes owner_id
  (50-100 mechanical changes, mostly s/`save_ai_bankroll(state)`/
  `save_ai_bankroll(state, owner_id=PLAYER_OWNER_ID)`/).

### Commit 4: Lobby seed migration to first-access

- Remove the boot-time `ensure_lobby_seeded(owner_id='_legacy')`
  call from app startup.
- Add a `_ensure_lobby_seeded_for(owner_id)` helper in cash_routes.py
  that's idempotent + fast (checks `cash_table_repo.list_all_tables(
  owner_id)` for existing rows; only seeds if empty).
- Call it from `/api/cash/state`, `/api/cash/lobby`, and
  `/api/cash/sponsor-offers` (every route that needs a populated
  lobby).
- Tests: first-access seeds a fresh lobby for a new owner; second
  access is a no-op; admin endpoint aggregates across owners.

### Commit 5: Chip ledger context_json owner_id stamping

- Every `ledger.record(...)` call site adds `owner_id` to the context
  dict so the audit's per-owner scoping works for ledger events too.
- Migration backfills existing rows with `owner_id='_legacy'` in the
  new dedicated column (already added in Commit 1; this commit
  populates it from `context_json.owner_id` when present).
- Audit's per-reason buckets stay global (cross-owner) but the
  `by_reason_window_24h` can scope to a single owner when requested.
- Tests: audit drift = 0 per-owner after a full session lifecycle in
  a fresh per-owner sandbox; admin endpoint sums correctly across
  multiple owners.

## Tests — what to expect

The test surface grows because every fixture that seeds AI state
needs an owner. Estimated breakdown:

| File | Change kind | Approx count |
|---|---|---|
| `test_cash_lobby_route.py`, `test_cash_lobby_integration.py`, `test_cash_sit_route.py`, `test_cash_sponsor_routes.py`, `test_cash_default_route.py`, `test_cash_lobby_tier.py`, `test_cash_cutover_integration.py`, `test_fast_forward.py` | Add `owner_id=PLAYER_OWNER_ID` to `save_ai_bankroll` calls; pass `owner_id` to `ensure_lobby_seeded` | ~40 |
| `test_personality_offers.py`, `test_personality_offers_tier.py` | Fake `BankrollRepository` honors owner_id | ~10 |
| `test_chip_ledger_audit.py`, `test_chip_ledger_lobby_seed.py`, `test_chip_ledger_destruction.py`, `test_chip_ledger_instrumentation.py`, `test_chip_ledger_wrapper_audit.py` | Scope sums; verify per-owner isolation | ~20 |
| `test_schema_migration_v99.py` | New file — covers v99 migration + backfill | 6-8 new tests |
| `test_cash_per_player_isolation.py` | New file — covers cross-owner isolation invariants | 8-12 new tests |

Net add: ~80 test changes + ~15 new tests.

## Risks

1. **Chip-ledger backfill semantics.** Option 1 (synthetic
   `_legacy` owner + fresh per-player seeds) keeps drift invariant
   at migration time. But the first-access seed for each new
   player creates AI bankrolls out of thin air — those need
   `ai_seed` ledger entries (the missing surface flagged in the
   economy doc's "Known issues" §2). This commit is a good time to
   close that gap.

2. **Cross-owner test pollution.** The hardcoded-repo-list pattern
   in five test fixtures (mock_init_persistence) was tripped over
   in Phase 2 — five files needed updates. Per-player adds another
   layer: tests that seed AI state without owner_id will silently
   write under `_legacy` and not be queried by per-owner reads.
   The default-to-`_legacy` back-compat from Commit 1 mitigates
   this but doesn't eliminate it. **Mitigation**: Commit 3 drops
   the default kwarg, forcing every call site to be explicit.

3. **Lobby seeding cost on first access.** Currently the boot hook
   amortizes one seed pass over the process lifetime. Per-player
   first-access seeding moves that cost to the first request per
   player. For 5 lobby tables × 4 baseline AI seats + relationship
   lookups + bankroll projections, that's maybe 50ms per first
   access. Acceptable; can be backgrounded if needed.

4. **Phase 4 design assumes shared world by default.** The Phase 4
   spec talks about AIs staking each other across the whole pool.
   Per-player scoping changes "the whole pool" to "this player's
   pool of AIs." The Phase 4 spec text doesn't need rewriting —
   the implementation just picks up `owner_id` as another
   parameter. But the per-player narrative shifts from "watch the
   casino's economy" to "your casino, your AIs."

5. **Audit's `_sum_active_loans` fallback.** The audit currently
   reads from `active_loan_amount` (legacy) when `stake_repo` is
   None (test back-compat). The legacy `player_bankroll_state`
   surface is per-player already, so no scoping change needed
   there. But when the new `_sum_active_stake_principal_for_humans`
   path runs (when stake_repo is provided), the sum needs to scope
   to the audit's `owner_id` filter — easy add.

## Files to read first

1. **This doc** — design above.
2. **`docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md`** — locks
   what Phase 2.5 builds on. Especially the "What's shipped" section
   and the suggested ship order.
3. **`docs/technical/CASH_MODE_ECONOMY.md`** — chip-bearing surfaces
   inventory. Per-player changes the shape of every surface listed.
4. **`poker/repositories/bankroll_repository.py`** — the biggest
   single file affected. The `save_ai_bankroll` /
   `load_ai_bankroll_current` / emotional state methods all need
   owner_id.
5. **`poker/repositories/cash_table_repository.py`** — table + idle
   pool CRUD. Composite-PK rewrite for v99.
6. **`cash_mode/lobby.py`** — `ensure_lobby_seeded` +
   `refresh_unseated_tables` are the main surfaces that need
   per-owner scoping.
7. **`flask_app/services/chip_ledger_audit.py`** — admin audit gets
   an optional `owner_id` filter; per-player audits become a new
   endpoint variant.
8. **`cash_mode/activity.py`** — global ring → per-owner buckets.
   The simplest example of the scoping pattern.
9. **`poker/repositories/relationship_repository.py`** (or wherever
   relationship_states lives) — the model to copy. It's already
   per-pair-keyed; Commit 1-3 mirrors its query patterns for
   AI runtime state.
10. **`flask_app/routes/cash_routes.py:_resolve_owner_id`** — every
    route already resolves owner_id at request-handler entry. The
    scoping is "pipe it down" rather than "compute it."
11. **`poker/repositories/schema_manager.py`** —
    `SCHEMA_VERSION = 98`; Phase 2.5 lands v99. Pattern for
    composite-PK rebuild lives at
    `_migrate_v27_fix_opponent_models_constraint`.

## Open questions

1. **Should per-player audits be a separate route or a query
   parameter?** `GET /api/admin/chip-ledger/audit` currently
   aggregates everything. Could be `GET /api/admin/chip-ledger/audit?owner_id=X`
   for a per-player view, or a separate `GET /api/cash/audit` that
   resolves the current user's owner_id from auth. Latter is
   cleaner — players don't need to know about owner_id semantics
   to inspect their own state.

2. **Cross-session AI events.** When Player A's Napoleon takes a
   bad beat from Player A's Bezos, that's a per-player event. But
   "Napoleon defaulted on Bezos" is an event with TWO AI sides —
   both have to be in the same owner's sandbox (which they are,
   since they're both Player A's instances). The activity ticker
   stays clean. No design change here, just confirmation.

3. **Personality template updates.** Today, editing Napoleon's
   `lender_profile` in `personalities.json` affects all sessions.
   That stays the same under per-player sandboxes — definitions
   are shared, only runtime state is scoped. If you want truly
   independent Napoleons (each player can tune their copy's
   profile), that's a different much-bigger project (per-player
   personality definitions, which already partially exists via
   the v64 `owner_id` + `visibility` on `personalities`).

4. **Migration of in-flight sessions.** If a player has a cash
   session active at Phase 2.5 deploy time, the migration's
   `_legacy` owner takes over. The session's `cash_personality_ids`
   mapping (game_data dict) still points to personality_ids that
   exist; their bankrolls are now under `_legacy`. The session
   continues using the `_legacy` owner's AI bankrolls until leave-
   time. Subsequent sit-downs hit the per-owner path. Documented
   as a known transitional state; tests cover the boundary.

5. **Frontend session continuity.** Per-player sandboxes mean
   logging in as a different user shows a different lobby. That's
   the design intent but worth confirming with the frontend
   product owner before shipping.

## Why this matters

The backing system's narrative is "your casino, your AIs, your
history of staking and being staked, your reputation graph." Without
per-player sandboxing, the AI economy is a *shared multiplayer-like
world* that just happens to have one player observing it. That's a
real design — but it's not the design locked decision #13 says we're
shipping ("Out of scope for v1. The cash-mode design is a
single-player sandbox.").

Per-player sandboxing makes the locked single-player framing
mechanically true: there *is* one player per casino, by construction.
Phase 4's AI-to-AI drama becomes drama within YOUR casino, not drama
in a shared room. That's a cleaner emotional model and one fewer
surprise when two users log in for the first time and discover
they're sharing Napoleon's mood swings.

The shared-world version still has its place — it's a Phase-6+
multiplayer feature. But the single-player baseline being a real
sandbox makes Phase 4 cleaner to build and Phase 5 (humans as
stakers) easier to scope (humans stake AIs *in their own sandbox*;
no question of "which Napoleon?").
