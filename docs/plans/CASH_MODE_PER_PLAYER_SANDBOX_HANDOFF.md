---
purpose: Phase 2.5 — introduce sandboxes as a first-class scoping unit for cash mode's runtime AI state (bankrolls, lobby tables, idle pool, activity history). Per-player in v1 via a 1:1 default sandbox; the model admits multiple-sandboxes-per-user, shared sandboxes, sandbox lifecycle (reset / export / archive), and admin-provided templates without further migration.
type: guide
created: 2026-05-20
last_updated: 2026-05-20
---

# Cash Mode — Sandbox Handoff (Phase 2.5)

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
> drama for Player B to watch. Sandbox scoping breaks that
> assumption — each sandbox only sees its own Napoleon staking its
> own Bezos. Building Phase 4 against the shared model and then
> retrofitting per-sandbox is much more work than doing the scoping
> first. The Phase 4 design's spec text doesn't change meaningfully;
> the implementation just picks up `sandbox_id` as another parameter
> and the activity ticker becomes per-bucket.

## Sandbox vs owner_id — why they're separate

Two distinct concepts that happen to be 1:1 in v1:

  - **`owner_id`** — answers "who is this account?". Identity. Comes
    from auth (Google OAuth `users.id`). Existing surface.
  - **`sandbox_id`** — answers "which save-file is this state in?".
    World state. New surface in v99. Scopes the runtime AI state
    (`ai_bankroll_state`, `cash_tables`, `cash_idle_pool`, ledger
    rows, activity ticker).

In v1, each user gets one default sandbox auto-created on first
cash-mode access. The route layer resolves `sandbox_id` from
`owner_id` via `_resolve_sandbox_for(owner_id)` and threads it
through. Players never see the distinction.

Future use cases that need the abstraction:

| Feature | Why owner-only doesn't work | Sandbox makes it cheap |
|---|---|---|
| "Reset my casino" | Would need to delete the user account | Delete the sandbox row, create a new one |
| Multiple save files per user | Need separate accounts | N sandboxes per owner |
| Export / import sandbox state | Coupled to user data | Clean serialization unit |
| Admin "tutorial" sandboxes | Awkward — admin user shared? | Owner = `_system`, anyone forks |
| Shared / co-op sandboxes | Owner conflict | N:1 owners-to-sandbox via future join table |
| AI character lifecycle | Tangled with auth | Sandbox archival doesn't touch the user |

The third axis that composes naturally with this:

| Field | Lives on | Answers |
|---|---|---|
| `personalities.owner_id` (v64 — exists) | `personalities` | Who CREATED this personality definition? |
| `personalities.visibility` (v64 — exists) | `personalities` | Who can SEE / instantiate this definition? |
| `sandbox_id` (v99 — NEW) | runtime-state tables | Which world holds this AI's bankroll / mood / seat? |

A `public` personality template can be instantiated in many
sandboxes simultaneously. Each instance has its own bankroll +
emotional state + relationship history scoped to that sandbox.
Phase 5+'s "player creates a custom personality" feature already
has its half-shipped (personalities.owner_id / visibility); v99's
sandbox scoping completes the picture.

## What's already scoped vs shared

| Surface | Status | Why it matters |
|---|---|---|
| `player_bankroll_state` | Per-player (keyed on `player_id`) | ✓ player bankroll is part of identity, not sandbox state |
| `stakes` | Per-borrower (keyed on `borrower_id`) | ✓ borrower side fine; staker side gets `sandbox_id` this phase |
| `relationship_states` | Per-pair (`observer_id`, `opponent_id`) | ✓ already the per-axis-keyed model to copy |
| `games` table (cash sessions) | Per-`owner_id` | ✓ identity-scoped; sandbox_id added as a context field |
| `personalities` (definitions) | Per-`owner_id` + visibility | ✓ public personalities stay shared as definitions; sandbox holds INSTANCES |
| `ai_bankroll_state` | **Shared** by `personality_id` only | One Napoleon, one bankroll across all worlds. The biggest sticking point. |
| `ai_bankroll_state.emotional_state_json` | **Shared** | Napoleon's tilt is global — Player A's bad beat affects Player B's Napoleon |
| `cash_tables` | **Shared** (5 global tables) | All worlds see the same lobby rosters |
| `cash_idle_pool` | **Shared** | AIs between sessions globally tracked |
| `chip_ledger_entries` | **Global stream** | source/sink encoding is `ai:<pid>` — doesn't carry sandbox |
| Activity ticker (`cash_mode/activity.py`) | **Shared** (in-memory ring) | "Bezos staked Napoleon" event is global |

## Design — sandboxed Napoleon

Each sandbox holds its own copy of every AI personality's *runtime
state*: bankroll, idle status, emotional state, table assignment.
The personality *definition* (traits, play style, lender profile in
config_json) stays shared via the existing `personalities` table.

The mental model: like single-player save files in a video game.
Sandbox A's Napoleon is a separate save-state from Sandbox B's
Napoleon — same character template, different bankroll histories,
different emotional baggage, different memories of the player.

This is intentionally NOT a multi-tenant database split. We stay in
one SQLite file, one schema, with `sandbox_id` columns scoping the
runtime surfaces. Simpler ops, simpler audit (admins can still
aggregate across sandboxes), and the relationship layer's already
per-pair-keyed so the surfaces compose naturally.

V1 ships with one sandbox per `owner_id`, auto-created on first
cash-mode access. Players never see the sandbox abstraction in the
UI — every cash route resolves `sandbox_id` from auth-derived
`owner_id` via a one-line helper. The data model is ready for
multi-sandbox / sandbox-management UI whenever that ships.

## Schema scope (v99)

**Pre-launch, single environment.** v99 truncates the affected
runtime-state tables and rebuilds with `sandbox_id` as part of the
key. No backfill bookkeeping, no synthetic `_legacy` sandbox, no
chip-conservation gymnastics. Fresh-start migration; first cash-mode
access per user creates their default sandbox and seeds it from
scratch.

```sql
-- v99: sandboxes as first-class scoping units. Pre-launch fresh-
-- start migration — truncates runtime AI state and rebuilds the
-- key shape. The migration is *destructive* by design; we have one
-- environment and no real production data to preserve.

-- 1. New `sandboxes` table — one row per save-file. V1 creates one
-- per owner_id on first cash-mode access; future multi-sandbox UI
-- lets a single owner have several.
CREATE TABLE sandboxes (
    sandbox_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,             -- whose sandbox (v1: 1:1; future: N:1)
    name TEXT NOT NULL,                 -- 'My Casino' default; user-renamable later
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    archived_at TIMESTAMP               -- soft-delete affordance for reset / archive
);
CREATE INDEX idx_sandboxes_owner ON sandboxes(owner_id) WHERE archived_at IS NULL;

-- 2. Truncate + rebuild runtime AI state with sandbox_id in the PK.
-- SQLite ALTER PRIMARY KEY isn't supported, so the migration drops
-- and recreates these three tables. The pre-launch fresh-start
-- decision makes the data loss intentional.
DROP TABLE IF EXISTS ai_bankroll_state;
CREATE TABLE ai_bankroll_state (
    personality_id TEXT NOT NULL,
    sandbox_id TEXT NOT NULL,
    chips INTEGER NOT NULL DEFAULT 0,
    last_regen_tick TIMESTAMP,
    emotional_state_json TEXT,
    PRIMARY KEY (personality_id, sandbox_id)
);
CREATE INDEX idx_ai_bankroll_sandbox ON ai_bankroll_state(sandbox_id);

DROP TABLE IF EXISTS cash_tables;
CREATE TABLE cash_tables (
    table_id TEXT NOT NULL,
    sandbox_id TEXT NOT NULL,
    stake_label TEXT NOT NULL,
    seats_json TEXT NOT NULL,
    dealer_idx INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (table_id, sandbox_id)
);
CREATE INDEX idx_cash_tables_sandbox ON cash_tables(sandbox_id);

DROP TABLE IF EXISTS cash_idle_pool;
CREATE TABLE cash_idle_pool (
    personality_id TEXT NOT NULL,
    sandbox_id TEXT NOT NULL,
    left_at TIMESTAMP NOT NULL,
    reason TEXT NOT NULL,
    target_stake TEXT,
    PRIMARY KEY (personality_id, sandbox_id)
);
CREATE INDEX idx_cash_idle_sandbox ON cash_idle_pool(sandbox_id);

-- 3. chip_ledger_entries gets sandbox_id as a *context* column
-- (preserve existing rows — the ledger is append-only history we
-- don't want to nuke even pre-launch; it's the audit's foundation).
ALTER TABLE chip_ledger_entries ADD COLUMN sandbox_id TEXT;
CREATE INDEX idx_chip_ledger_sandbox ON chip_ledger_entries(sandbox_id);
-- Existing rows have sandbox_id=NULL; the audit treats NULL as the
-- pre-migration universe and bundles into a single "_pre_v99" bucket
-- for the cross-sandbox totals. New writes always stamp sandbox_id.
```

**Why we keep the chip ledger.** The other three tables hold mutable
state we can rebuild. The ledger is observability history — even
pre-launch, losing it makes the audit's drift baseline confusing.
Pre-existing rows survive with `sandbox_id=NULL`; the audit treats
those as a pre-v99 bucket for cross-sandbox aggregations and ignores
them for per-sandbox queries.

**What players lose on upgrade.** Any AI bankroll regen-accumulated
chips beyond starting balances. Any active cash sessions that
existed at migration time become unrecoverable (they reference
`personality_id`s whose bankrolls are gone). Acceptable because:
(a) pre-launch, no real users, (b) the cash-mode work itself is in
testing, (c) fresh sandboxes give every user a clean start under the
new economy semantics anyway.

## Commit breakdown (~6 commits)

### Commit 1: Sandbox table + SandboxRepository + auth helper

- v99 migration **part 1**: `CREATE TABLE sandboxes`. No seed row
  needed — fresh-start migration; first real owner who hits cash
  mode triggers their own sandbox creation.
- New `poker/repositories/sandbox_repository.py`:
  - `SandboxRepository.create(owner_id, name)` → returns a
    fresh `sandbox_id` (uuid4). The repo owns id generation so
    callers never construct their own.
  - `SandboxRepository.load(sandbox_id)` — fetch one.
  - `SandboxRepository.list_for_owner(owner_id, include_archived=False)`.
  - `SandboxRepository.archive(sandbox_id, now)` — soft-delete.
- New `flask_app/services/sandbox_resolver.py`:
  - `resolve_default_sandbox_for(owner_id, *, sandbox_repo) → str`.
    Looks up `list_for_owner(owner_id)[0]` if present; else calls
    `sandbox_repo.create(owner_id, name='My Casino')` and returns
    the new opaque id. Cached per-process in a `Dict[owner_id,
    sandbox_id]` so hot-path resolution is O(1) after warmup.
- Wired into `create_repos()` + `flask_app.extensions`.
- Tests: round-trip CRUD; ids are opaque uuid4 (regex check);
  create returns distinct ids on repeated calls; archive marks the
  row; resolver creates on first-access, returns existing row on
  second.

### Commit 2: Schema v99 part 2 — scope existing tables

- v99 migration **part 2**: add `sandbox_id` column + indexes to
  `ai_bankroll_state`, `cash_tables`, `cash_idle_pool`,
  `chip_ledger_entries`. PK recreation done via the standard SQLite
  table-rebuild pattern (used by v27).
- Backfill: every existing row gets `sandbox_id='_legacy'`.
- Update repository method signatures to take `sandbox_id` as a
  required kwarg. Existing callers WILL break — Commit 3 catches
  them all at compile time. Doing it as a required kwarg (not
  default-to-`'_legacy'`) forces every site to be explicit; the
  default-kwarg trick from earlier phases led to silent fallback
  bugs we don't want to repeat.
- Methods affected:
  - `BankrollRepository.save_ai_bankroll(state, *, sandbox_id)`
  - `BankrollRepository.load_ai_bankroll(personality_id, *, sandbox_id)`
  - `BankrollRepository.load_ai_bankroll_current(personality_id, *, sandbox_id, now=None)`
  - `BankrollRepository.iter_personality_ids_with_bankrolls(*, sandbox_id=None)` — None = ALL (admin audit).
  - `BankrollRepository.sum_ai_bankroll_chips_stored(*, sandbox_id=None)`
  - `BankrollRepository.save_emotional_state_json(personality_id, blob, *, sandbox_id)`
  - `BankrollRepository.load_emotional_state_json(personality_id, *, sandbox_id)`
  - `BankrollRepository.load_emotional_state_json_for_pids(pids, *, sandbox_id)`
  - `CashTableRepository.list_all_tables(*, sandbox_id=None)` — None for admin
  - `CashTableRepository.load_table(table_id, *, sandbox_id)`
  - `CashTableRepository.save_table(state, *, sandbox_id, now=None)`
  - `CashTableRepository.save_idle(entry, *, sandbox_id)`
  - `CashTableRepository.load_idle(personality_id, *, sandbox_id)`
  - `CashTableRepository.list_idle(*, sandbox_id=None)`
  - `CashTableRepository.delete_idle(personality_id, *, sandbox_id)`
- Tests: schema round-trip + per-sandbox isolation (writing under
  one sandbox doesn't surface under another).

### Commit 3: Wire sandbox_id through cash_mode pure helpers

- `cash_mode/lobby.py`:
  - `ensure_lobby_seeded(*, sandbox_id, ...)` — required kwarg.
    Each sandbox gets its own seed pass.
  - `refresh_unseated_tables(*, sandbox_id, ...)` — scopes the refresh.
  - `_table_id_for_stake(stake_label, sandbox_id)` — table IDs
    become `cash-table-<stake>-<sandbox_slug>-001` so the
    (table_id, sandbox_id) PK doesn't conflict across sandboxes.
- `cash_mode/movement.py`:
  - `refresh_table_roster(*, sandbox_id, ...)` — bankroll lookups
    pass sandbox_id through.
- `cash_mode/sponsor_offers.py`:
  - `compute_personality_offers(*, sandbox_id, ...)` — bankroll
    loads use it. The `candidate_personalities` list passed in
    already comes from the player's eligible pool
    (`personality_repo.list_eligible_for_cash_mode(user_id=owner_id)`
    — note this stays owner-scoped because the personality DEFINITION
    visibility is owner-bound, not sandbox-bound).
- `cash_mode/full_sim.py`:
  - `play_one_hand(*, sandbox_id, ...)` — sim chips come from
    per-sandbox bankrolls. AI psychology persistence goes
    per-sandbox too.
- `cash_mode/staking_tier.py`:
  - No change (already scoped by borrower_id which IS the player).
- `cash_mode/stake_chip_flow.py`:
  - No change (operates on a Stake object that doesn't need to know
    about sandbox scoping — the route layer handles that).

### Commit 4: Wire sandbox resolution through routes + handlers

- Pattern at every cash route entry:
  ```python
  owner_id = _resolve_owner_id()                       # existing
  sandbox_id = resolve_default_sandbox_for(            # new
      owner_id, sandbox_repo=sandbox_repo,
  )
  ```
  Then `sandbox_id` is threaded through to every repo / cash_mode
  call. The resolver caches in-process so the per-request cost is
  one dict lookup after warmup.
- `flask_app/routes/cash_routes.py`: every place that calls
  `bankroll_repo.load_ai_bankroll_*`, `cash_table_repo.*`, or
  `cash_mode.lobby.*` passes sandbox_id.
- `flask_app/handlers/game_handler.py`: hand-boundary hooks that
  refresh tables / project bankrolls.
- Game session ↔ sandbox link: stamp `sandbox_id` onto the
  `game_data` dict at sit-down (`sponsor_and_sit`), so leave-time
  + hand-boundary handlers don't need to re-resolve. Resolves the
  "session outlives the user's auth session" concern.
- Boot hook (`ensure_lobby_seeded` at app startup) **removes**. The
  first-access pattern is the new model — first `/api/cash/lobby`
  or `/api/cash/state` per sandbox seeds its lobby lazily.
- `flask_app/services/chip_ledger_audit.py`:
  - `compute_audit(*, sandbox_id=None, ...)` — `None` means
    "aggregate across all sandboxes" (admin endpoint).
  - When `sandbox_id` is provided, every sum query filters:
    `WHERE sandbox_id = ?`.
- `cash_mode/activity.py`:
  - Replace the global ring buffer with `_buckets: Dict[str, deque]`
    keyed on `sandbox_id`.
  - `recent_events(sandbox_id, limit=5)`.
  - `record_event(sandbox_id, ...)`.
- Tests: every fixture that seeds AI bankrolls now passes
  sandbox_id (50-100 mechanical changes, mostly
  s/`save_ai_bankroll(state)`/
  `save_ai_bankroll(state, sandbox_id=SANDBOX_ID)`/).

### Commit 5: Lobby seed migration to first-access

- Remove the boot-time `ensure_lobby_seeded(sandbox_id='_legacy')`
  call from app startup.
- Add a `_ensure_lobby_seeded_for(sandbox_id)` helper in
  cash_routes.py that's idempotent + fast (checks
  `cash_table_repo.list_all_tables(sandbox_id=...)` for existing
  rows; only seeds if empty).
- Call it from `/api/cash/state`, `/api/cash/lobby`, and
  `/api/cash/sponsor-offers` (every route that needs a populated
  lobby).
- Tests: first-access seeds a fresh lobby for a new sandbox;
  second access is a no-op; admin audit aggregates across sandboxes.

### Commit 6: Chip ledger context_json sandbox_id stamping

- Every `ledger.record(...)` call site adds `sandbox_id` to the
  context dict + the new column so the audit's per-sandbox scoping
  works for ledger events too.
- Migration backfills existing rows with `sandbox_id='_legacy'`
  in the dedicated column added in Commit 2.
- Audit's per-reason buckets stay global (cross-sandbox) but the
  `by_reason_window_24h` can scope to a single sandbox when
  requested.
- Tests: audit drift = 0 per-sandbox after a full session lifecycle
  in a fresh sandbox; admin endpoint sums correctly across multiple
  sandboxes.

## Tests — what to expect

The test surface grows because every fixture that seeds AI state
needs a sandbox. Estimated breakdown:

| File | Change kind | Approx count |
|---|---|---|
| `test_cash_lobby_route.py`, `test_cash_lobby_integration.py`, `test_cash_sit_route.py`, `test_cash_sponsor_routes.py`, `test_cash_default_route.py`, `test_cash_lobby_tier.py`, `test_cash_cutover_integration.py`, `test_fast_forward.py` | Resolve a sandbox in setUp; pass `sandbox_id=...` to `save_ai_bankroll`, `ensure_lobby_seeded`, etc. | ~50 |
| `test_personality_offers.py`, `test_personality_offers_tier.py` | Fake `BankrollRepository` honors sandbox_id | ~10 |
| `test_chip_ledger_audit.py`, `test_chip_ledger_lobby_seed.py`, `test_chip_ledger_destruction.py`, `test_chip_ledger_instrumentation.py`, `test_chip_ledger_wrapper_audit.py` | Scope sums; verify per-sandbox isolation; NULL-bucket handling for legacy ledger rows | ~25 |
| `test_schema_migration_v99.py` | New file — table-rebuild correctness + sandbox FK semantics | 8-10 new tests |
| `test_sandbox_repository.py` | New file — sandbox CRUD + archive + resolver | 8-10 new tests |
| `test_cash_per_sandbox_isolation.py` | New file — cross-sandbox isolation invariants (AI bankroll in sandbox A invisible to sandbox B, etc.) | 8-12 new tests |

Net add: ~85 test changes + ~25-30 new tests.

## Risks

1. **First-access AI bankroll seeding fires no `ai_seed` ledger
   entry today.** The existing seed path (`save_ai_bankroll` for a
   personality with no prior row) creates chips out of thin air
   without a ledger annotation. The economy doc's "Known issues"
   §2 flagged this pre-Phase-2.5; v99 makes it worse because
   *every new sandbox* triggers a seed wave. **Mitigation**:
   Commit 2 adds an `ai_seed` ledger reason + fires it in
   `BankrollRepository.save_ai_bankroll` when the personality has
   no prior row in this sandbox. Closes the audit-drift leak.

2. **Cross-sandbox test pollution.** The hardcoded-repo-list
   pattern in five test fixtures (mock_init_persistence) was
   tripped over in Phase 2 — five files needed updates. Sandbox
   scoping adds another layer: tests that seed AI state without
   `sandbox_id` will fail outright (required kwarg, no default).
   That's the chosen mitigation — fail loud, not silent. Better
   than the default-to-`_legacy` trick which would let pollution
   sneak in unnoticed.

3. **Lobby seeding cost on first access.** Currently the boot hook
   amortizes one seed pass over the process lifetime. Per-sandbox
   first-access seeding moves that cost to the first request per
   sandbox. For 5 lobby tables × 4 baseline AI seats + relationship
   lookups + bankroll projections, that's maybe 50ms per first
   access. Acceptable; can be backgrounded if needed.

4. **Phase 4 design assumes shared world by default.** The Phase 4
   spec talks about AIs staking each other across the whole pool.
   Sandbox scoping changes "the whole pool" to "this sandbox's pool
   of AIs." The Phase 4 spec text doesn't need rewriting — the
   implementation just picks up `sandbox_id` as another parameter.
   But the narrative shifts from "watch the casino's economy" to
   "your casino, your AIs." Confirmed acceptable given locked
   decision #13 (single-player v1).

5. **Audit's `_sum_active_stake_principal_for_humans` scoping.**
   The audit's new query (added post-Phase-2-cutover) sums
   `stakes.principal WHERE status='active' AND borrower_kind='human'`
   globally. Add `AND sandbox_id = ?` when the audit is called
   per-sandbox. Easy edit, but it's the kind of thing that's easy
   to miss when the audit is silently working at the cross-sandbox
   level.

6. **Destructive migration in a non-prod-but-shared dev env.**
   The "single environment, can nuke" assumption (per the
   2026-05-20 decision) means existing teammates' local dev DBs
   lose their AI bankroll state on pull. Coordinate via Slack /
   commit message; the migration is one-shot so it only bites
   once. The chip ledger survives (its rows are append-only
   history) so admin observability is preserved.

## Files to read first

1. **This doc** — design above.
2. **`docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md`** — locks
   what Phase 2.5 builds on. Especially the "What's shipped" section
   and the suggested ship order.
3. **`docs/technical/CASH_MODE_ECONOMY.md`** — chip-bearing surfaces
   inventory. Sandbox scoping changes the shape of every surface
   listed.
4. **`poker/repositories/bankroll_repository.py`** — the biggest
   single file affected. The `save_ai_bankroll` /
   `load_ai_bankroll_current` / emotional state methods all need
   sandbox_id.
5. **`poker/repositories/cash_table_repository.py`** — table + idle
   pool CRUD. Composite-PK rewrite for v99.
6. **`cash_mode/lobby.py`** — `ensure_lobby_seeded` +
   `refresh_unseated_tables` are the main surfaces that need
   per-sandbox scoping.
7. **`flask_app/services/chip_ledger_audit.py`** — admin audit gets
   an optional `sandbox_id` filter; per-sandbox audits become a new
   endpoint variant.
8. **`cash_mode/activity.py`** — global ring → per-sandbox buckets.
   The simplest example of the scoping pattern.
9. **`poker/repositories/relationship_repository.py`** (or wherever
   relationship_states lives) — the per-pair-keyed model to copy.
   Commit 2-3 mirrors its query patterns for sandbox-scoped AI
   runtime state.
10. **`flask_app/routes/cash_routes.py:_resolve_owner_id`** — every
    route already resolves owner_id at request-handler entry. The
    new sandbox resolution slots in immediately after.
11. **`poker/repositories/schema_manager.py`** —
    `SCHEMA_VERSION = 98`; Phase 2.5 lands v99. Pattern for
    composite-PK rebuild lives at
    `_migrate_v27_fix_opponent_models_constraint`.

## Locked decisions

1. **Sandbox id is opaque UUID4.** Generated by
   `SandboxRepository.create()`; never derivable from `owner_id`.
   The resolver caches `owner_id → sandbox_id` in-process so the
   per-request lookup is ~one hash-map hit after warmup. Locked
   2026-05-20 to keep sandbox identity decoupled from auth identity
   — deterministic ids would force a one-shot rename migration the
   first time multi-sandbox UI ships.

2. **Pre-launch destructive migration.** v99 drops + recreates
   `ai_bankroll_state`, `cash_tables`, `cash_idle_pool` rather than
   ALTER + backfill. `chip_ledger_entries` survives with
   `sandbox_id=NULL` on legacy rows. Locked 2026-05-20 — single
   environment, no real production data.

3. **`sandbox_id` is a required kwarg on every scoped repo method.**
   No default-to-`_legacy` back-compat trick. Forces every call
   site to be explicit; pollution from forgotten kwargs fails
   loud instead of silently writing to a default bucket. Locked
   2026-05-20.

## Open questions

1. **Per-sandbox audits — separate route or query parameter?**
   `GET /api/admin/chip-ledger/audit` currently aggregates
   everything. Could be
   `GET /api/admin/chip-ledger/audit?sandbox_id=X` for a per-
   sandbox view, or a separate `GET /api/cash/audit` that resolves
   the current user's sandbox_id from auth. Latter is cleaner —
   players don't need to know about sandbox_id semantics to
   inspect their own state.

3. **Cross-AI events stay within a sandbox.** When Sandbox A's
   Napoleon takes a bad beat from Sandbox A's Bezos, that's a
   per-sandbox event. "Napoleon defaulted on Bezos" is an event
   with TWO AI sides — both are in the same sandbox by
   construction (a sandbox holds ALL its AI instances). The
   activity ticker stays clean. No design change here, just
   confirmation.

4. **Personality template updates.** Today, editing Napoleon's
   `lender_profile` in `personalities.json` affects all sessions.
   That stays the same under sandbox scoping — definitions are
   shared, only runtime state is scoped. If you want truly
   independent Napoleons (each sandbox can tune their copy's
   profile), that's a different much-bigger project (per-sandbox
   personality definitions, which would also need a v64-style
   visibility model — out of scope here).

5. **Frontend session continuity.** Sandbox scoping (even with v1's
   1:1 default sandbox per user) means logging in as a different
   user shows a different lobby. That's the design intent but
   worth confirming with the frontend product owner before
   shipping.

6. **When to expose sandbox management UI?** Spec ships data-model
   ready for multi-sandbox; UI is deferred. Triggers worth watching
   in playtest: (a) users asking for "reset my casino", (b) users
   wanting a separate experimental sandbox to test wild stake
   strategies, (c) admin asking for tutorial-sandbox templates.
   None of those need schema changes — just a route + UI work.

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
