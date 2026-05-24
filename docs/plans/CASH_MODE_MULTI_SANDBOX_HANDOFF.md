---
purpose: Extend the per-player sandbox foundation (Phase 2.5) to support multiple sandboxes per account as a power-user feature, each with its own starting conditions and tunable knobs, so players can run distinct cash-mode playthroughs without losing earlier worlds.
type: guide
created: 2026-05-24
last_updated: 2026-05-24
---

# Cash Mode — Multi-Sandbox Support (Power-User Feature)

> **Read first:**
> - [`CASH_MODE_PER_PLAYER_SANDBOX_HANDOFF.md`](CASH_MODE_PER_PLAYER_SANDBOX_HANDOFF.md)
>   — Phase 2.5, ships the `sandboxes` table and 1:1 owner→sandbox
>   resolver. This doc picks up where that one left off.
> - [`CASH_MODE_ECONOMY.md`](../technical/CASH_MODE_ECONOMY.md) —
>   describes the chip-bearing surfaces that each sandbox will scope.
> - [`CASH_MODE_BACKING_SYSTEM_HANDOFF.md`](CASH_MODE_BACKING_SYSTEM_HANDOFF.md)
>   — locks the staking-economy phasing; multi-sandbox is orthogonal
>   to it and should not affect Phase 3+ work.

## Intent

Today every account has exactly one sandbox, auto-created on first
cash-mode access. The data model already admits N sandboxes per
owner for the runtime AI state that was scoped in Phase 2.5 —
`ai_bankroll_state`, `cash_tables`, `cash_pair_stats`,
`ai_vice_state`, `cash_idle_pool` all have `sandbox_id` in their
primary key, and `SandboxRepository.list_for_owner` already
returns a list. The chokepoint is
`flask_app/services/sandbox_resolver.py` which always returns the
first live sandbox (or creates one).

This work opens that chokepoint and exposes a small set of
per-sandbox knobs so players can run **distinct playthroughs**
("Casual seed", "Hardcore rake-on, regen-off", "Sharp-bots-only,
high-roller stakes") side-by-side without one bleeding into the
other.

**Important caveat that changed the plan.** When Phase 2.5
shipped, the staking-economy work (Phase 3+ of the backing-system
roadmap) was still ahead. It is now live, and its tables
(`stakes` and related) were never sandbox-scoped. That means
staking events, carries, tier degradation, and net-worth history
will bleed across sandboxes as soon as a single owner has more
than one. Multi-sandbox is therefore **not orthogonal to the
backing system anymore** — staking scoping must ship with this
work, not after.

## Effort framing (read first)

The work splits into three tiers. Tiers 0 and 1 ship together as
Phase 1 of this feature. Tier 2 is deferred.

- **Tier 0 — Cheap correctness fixes (~half a day).** Auth-leak
  guards on the resolver, deterministic fallback ordering,
  mid-session activation block, fixing the knob-wiring hooks.
  Independent of any data-model change; should ship regardless.
- **Tier 1 — Staking scoping (~2-3 days).** Destructive migration
  on `stakes` (and audit the related staking tables) to add
  `sandbox_id`. Thread it through `stake_repository.py` and the
  Phase 3+ business logic. Same shape as the v109
  `cash_pair_stats` migration.
- **Tier 2 — Human-bankroll scoping (deferred, ~3-5 days if we
  ever do it).** Scope `player_bankroll_state` per sandbox. See
  "Deferred" below for the design rationale on skipping this.

Honest effort estimate: original draft suggested "~1 week MVP."
Real cost with staking scoping included is closer to **~2 weeks**.

## Goals

- A single account can own N live sandboxes, each with its own
  AI bankrolls / tables / pair-stats / vice state / staking
  history. Phase 2.5 already scoped the first four; this work
  adds staking scoping (Tier 1).
- Each sandbox carries a small bag of **starting-condition
  overrides** (wealth multiplier, bot-type defaults, economy flags,
  personality pool, RNG seed) stored as opaque JSON on the
  `sandboxes` row.
- Players can list, create, switch, and archive their own
  sandboxes via a simple admin-style page.
- The active sandbox for a request is resolved from session state
  with a sensible fallback chain. Every resolve re-validates
  ownership and live status so a stale or hijacked
  `active_sandbox_id` can never leak across owners (Tier 0).
- Existing single-sandbox users see zero behavior change: NULL
  `settings_json` resolves to current defaults; the resolver's
  fallback path returns their one live sandbox exactly as today.

## Non-goals

- **Not a polished, in-game UX.** This is a power-user feature.
  A hidden `/sandboxes` admin page with a JSON editor and a few
  presets is enough for v1. New players never encounter it; the
  default sandbox flow stays identical.
- **Not per-sandbox human bankroll.** `player_bankroll_state`
  stays global. Sandboxes are framed as *configurable arenas*
  (your money is yours; the world is what changes), not
  *save-files* (fresh identity per world). This is a deliberate
  product call — see "Deferred" for the framing. Tier 2 reverses
  this if we ever change our mind.
- **Not per-sandbox playstyle remapping or per-sandbox psychology
  sensitivity.** Both are tightly coupled to personality anchors
  and global constants respectively, and deserve their own design
  pass. Deferred to Phase 2 of this work (see below).
- **Not cross-device sync of the active sandbox.** v1 uses Flask
  session state (cookie-scoped). If users complain about switching
  devices resetting their selection, add a `last_active_sandbox_id`
  column on `users` later.

## Why a power-user feature

Most players want one persistent world. Exposing a sandbox picker
to all users adds cognitive load and invites "did I just lose my
chips?" confusion. Power users — testers, streamers, anyone
exploring the game's economy — get real value from multiple worlds
and from tweaking starting conditions. Hiding the feature behind
an admin route keeps the default surface clean.

## Data model

Two changes: a `settings_json` column on `sandboxes` (additive),
and a destructive migration on the staking tables to add
`sandbox_id` to their PK.

### `sandboxes` — additive

The `sandboxes` table today is:

```sql
sandbox_id   TEXT PRIMARY KEY,
owner_id     TEXT NOT NULL,
name         TEXT NOT NULL,
created_at   TIMESTAMP NOT NULL,
archived_at  TIMESTAMP
```

**Add one column:**

```sql
settings_json TEXT  -- nullable; NULL means "use all defaults"
```

A single opaque JSON column avoids schema churn as new knobs land.
Schema validation happens in Python (`SandboxSettings` dataclass)
with unknown keys ignored on read so older code tolerates newer
sandboxes. Bump the schema version, add the column, no destructive
migration needed (existing sandboxes get `NULL`, resolve to
defaults).

### Staking tables — destructive (Tier 1)

`stakes` and the related staking-event / settlement tables were
introduced after the Phase 2.5 sandbox scoping work and were
never given a `sandbox_id`. With multi-sandbox live, a single
owner's Napoleon-stakes-Bezos history would bleed across every
sandbox unless we scope them now.

**Audit list** (verify before writing the migration — these are
the call sites the staking system touches; some may already
carry `sandbox_id` via `cash_sessions` joins and only need a
filter, not a column):

- `stakes` (the carry-relationship rows)
- Stake-event / settlement / forgiveness tables under
  `poker/repositories/stake_repository.py`
- Net-worth history rows
- Borrower-tier / lender-tier degradation state
- Any staking-derived view materialized into another table

**Migration shape:** modeled on the v109 `cash_pair_stats`
destructive migration. Add `sandbox_id` to the PK (or as a NOT
NULL column with a backfill if PK migration is too costly),
backfill from `cash_sessions.sandbox_id` where possible, drop
rows that can't be sandbox-attributed (or assign them to the
owner's default sandbox at migration time). Confirm with an
audit query that every staking row has a `sandbox_id` post-migration.

**Risk:** this is the same class of destructive migration that
took careful work at v102 and v109. Plan on a full audit pass
of `stake_repository.py` and its callers before writing the
migration. The migration is the riskiest single piece of work
in this whole plan.

## The knobs — Phase 1 MVP

Five knobs, all backed by mechanisms that already exist as globals
or per-personality fields. Each one is a small refactor to read
from sandbox context instead of a module constant.

| Knob | Effect | Backed by | Default |
|---|---|---|---|
| `wealth_multiplier` | Scales every personality's `starting_bankroll` and `bankroll_rate` on first AI seed in this sandbox | `personalities.config_json.bankroll_knobs`, applied via a sandbox-aware overlay on `load_personality_knobs(...)` so every seeding path (`ensure_ai_bankrolls_seeded`, `save_ai_bankroll` first-write, fresh-seat fallback) gets the multiplier consistently | `1.0` |
| `default_bot_type` | Sandbox-wide default for AI controller (`chaos` / `standard` / `lean` / `sharp` / `baseline_solver` / `casebot`); per-game override still wins | `flask_app/routes/game_routes.py` bot_type dispatch | `standard` |
| `economy_flags` | Per-sandbox override for `REGEN_ENABLED`, `RAKE_ENABLED`, `RAKE_PLAYER_TABLES`, `RAKE_RATE`, `RAKE_CAP_BB` | `cash_mode/economy_flags.py` module globals | inherit current globals |
| `personality_pool` | Optional allowlist of personality IDs (or tier labels) eligible to spawn in this sandbox's lobby | `cash_mode/seating.py` query | `None` (all eligible) |
| `rng_seed` | Sandbox-level seed for table/seating randomness — enables reproducible scenarios | `poker/poker_game.py` per-game seed parameter | `None` (current per-game randomness) |

These five together already give "playthroughs feel distinct"
without touching any of the harder-to-decouple subsystems.

## Phase 2 (after Phase 1 lands and gets used)

These are higher-value but need real engineering. Worth doing only
after we see which knobs Phase 1 users actually reach for.

- **Per-personality bankroll overrides** — instead of a single
  multiplier, let a sandbox override `starting_bankroll` per
  personality (JSON map). Useful for "Napoleon starts broke"
  scenarios.
- **Psychology sensitivity multiplier** — a single 0.5x–2x dial on
  `DRIFT_BASE_SIGMA` and `_get_severity_floor()` thresholds in
  `poker/psychology_model.py` / `poker/zone_config.py`. Cheapest
  version of "make the table more or less dramatic." Threading
  sandbox context through the psychology pipeline is the real cost.
- **Stakes ladder remix** — override `STAKES_LADDER` per sandbox
  (`cash_mode/stakes_ladder.py`). Enables "micro only" or "nosebleed
  only" worlds.
- **Table seat count** — `TABLE_SEAT_COUNT = 6` in
  `cash_mode/tables.py`; per-sandbox would enable 9-max or HU
  sandboxes.
- **Event severity floors** — direct override of
  `zone_config._get_severity_floor()` per event for fine-grained
  drama tuning.

## Deferred (intentionally out of scope)

- **Per-sandbox human bankroll (Tier 2)** —
  `player_bankroll_state` stays global in v1. The framing
  question: are sandboxes *save-files* (fresh identity per world,
  human starts broke, climbs again) or *configurable arenas*
  (your money is yours, you choose which world to spend it in)?
  Arena framing wins for v1 because (a) it preserves the literal
  stakes — your chips are real across worlds; (b) it matches the
  power-user feature framing (you're not roleplaying a fresh
  identity, you're choosing what kind of game to drop into); (c)
  it avoids a destructive migration on the human ledger and the
  UI redesign that would have to go with it ("which world's
  chips am I spending?"). If we ever want save-file framing,
  Tier 2 is: destructive migration on `player_bankroll_state` to
  add `sandbox_id` to PK; per-sandbox header chip display;
  sponsor/forgiveness flows thread sandbox context. Estimated
  3-5 days on top of Phase 1 + 2.
- **Per-sandbox playstyle remap** — playstyle derives from
  personality anchors (`baseline_aggression` + `baseline_looseness`)
  via `derive_primary_playstyle()`. Decoupling means an override
  layer between anchors and the derivation, which touches
  `poker/playstyle_selector.py`, `poker/bounded_options.py`
  (`STYLE_PROFILES`), and any consumer of the derived style. Worth
  doing eventually but not as part of this feature.
- **Per-sandbox closed-economy tuning** — `FAKE_VICE_*`,
  `GRINDER_HUNGER_THRESHOLD`, etc. The closed-loop economy is still
  experimental; exposing knobs before the system stabilizes invites
  cargo-cult tuning.

## Resolver / selection changes (Tier 0)

`flask_app/services/sandbox_resolver.py` today:

```
resolve_default_sandbox_for(owner_id):
  cache → list_for_owner → create
```

After this work:

```
resolve_active_sandbox_for(owner_id, session):
  1. If session['active_sandbox_id'] is set:
       re-validate (owner matches, archived_at IS NULL) on every
       call. If validation fails, drop it from session and fall
       through. Never trust a session-provided sandbox_id without
       this check — that's the auth-leak path.
  2. list_for_owner(owner_id) ORDER BY created_at ASC, sandbox_id ASC
     (deterministic — multi-worker default-creation races already
     produce duplicate "default" rows today; ordering must be
     stable so fallback is repeatable).
  3. If still empty: create + return.
```

The cache layer stays — keyed on `(owner_id, sandbox_id)` rather
than just `owner_id`. Cache invalidation triggers:

- archive (any active session pointing at this id falls through
  to fallback on next request)
- create (no invalidation needed; new id won't collide)
- `PATCH /api/sandboxes/<id>` (settings changed — invalidate the
  settings cache, not the active-sandbox cache)

**Mid-session activation guard.** Active cash games persist their
own `sandbox_id`. If the user switches active sandbox while a
cash session is live, lobby refresh / stake settlement / chip
ledger writes could resolve a different sandbox than the game's.
Two rules:

- `POST /api/sandboxes/<id>/activate` returns 409 if the caller
  has any live cash session (busy tables, mid-hand state). They
  must leave the table first.
- Any in-game route resolves sandbox from `game.sandbox_id`, not
  from session state. The session's active sandbox is for *lobby
  and pre-game* resolution only.

All existing cash routes already call
`_resolve_sandbox_id(owner_id)`; they point at the new function.
No route signature changes outside in-game routes that need to
prefer `game.sandbox_id`.

### Where settings get consumed

- `cash_mode/bankroll.py` — sandbox-aware overlay on
  `load_personality_knobs(...)` applies `wealth_multiplier` before
  any seeding path runs (`ensure_ai_bankrolls_seeded`,
  `save_ai_bankroll` first-write ledger, fresh-seat fallback).
  Overlay-at-the-source so we don't have to find every call site.
- `flask_app/routes/game_routes.py` — bot_type dispatch falls back
  to `sandbox_settings.default_bot_type` if the request doesn't
  specify.
- `cash_mode/economy_flags.py` — replace direct module-global reads
  with `economy_flag(name, sandbox_id)` helper that overlays
  sandbox overrides on top of module defaults. Note this is more
  invasive than it looks: `project_bankroll(...)` and similar
  projection paths currently don't carry sandbox context. Either
  thread `sandbox_id` through projection, or bake the effective
  flags into the per-personality knobs at load time (preferred —
  fewer call-site changes).
- `cash_mode/seating.py` — personality query filters on
  `sandbox_settings.personality_pool` when present.
- `poker/poker_game.py` — when creating a game in cash mode, mix
  `sandbox_settings.rng_seed` (if set) with the game-level seed so
  per-game variation persists within a deterministic sandbox.

## API surface

Five new routes, all under `/api/sandboxes`. All require an
authenticated owner; no admin role gate (this is a per-user
feature).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/sandboxes` | List caller's live sandboxes (id, name, created_at, active flag, brief settings summary) |
| `POST` | `/api/sandboxes` | Create new sandbox; body: `{name, settings?, preset?}`. Returns full row. |
| `POST` | `/api/sandboxes/<id>/activate` | Set session's active sandbox. 403 if not owned by caller, 404 if archived. |
| `PATCH` | `/api/sandboxes/<id>` | Update `name` and/or `settings_json`. Settings changes apply on next AI seed / next lobby refresh — does not retroactively scale existing bankrolls. |
| `POST` | `/api/sandboxes/<id>/archive` | Soft-delete (sets `archived_at`). If it was active, fall through to next-newest. |

## UI surface

One hidden page at `/sandboxes` (linked from a power-user menu or
just bookmarkable). Minimum viable layout:

- Table of caller's sandboxes (name, created, active radio, archive
  button)
- "Create new" form: name + preset dropdown (Casual / Hardcore /
  Chaos / Sharp / Custom) + collapsible JSON editor
- Active sandbox indicator in the cash-mode header (small text,
  click to open `/sandboxes`)

Presets are seed `SandboxSettings` dicts checked into code, not DB
rows. Three to four named presets cover most use cases:

- **Casual** — defaults (matches current behavior)
- **Hardcore** — regen off, rake on, wealth_multiplier 0.5
- **Chaos** — default_bot_type=chaos, personality_pool=fish+casino
- **Sharp** — default_bot_type=sharp, no presets on stakes
  (Phase 2 will add stakes remix)

## Migration / rollout

- **Schema (additive):** add `settings_json TEXT` column to
  `sandboxes`. Existing rows get `NULL`, which the resolver
  treats as "all defaults" — zero behavior change for current
  users.
- **Schema (destructive, Tier 1):** add `sandbox_id` to the
  staking tables identified in the audit. Backfill from
  `cash_sessions.sandbox_id` where possible; assign orphans to
  the owner's default sandbox. Verify with a post-migration
  audit query that no staking row has a NULL sandbox_id.
- **Backfill (non-staking):** none required.
- **Feature flag:** gate the `/sandboxes` page behind an env var
  (`ENABLE_MULTI_SANDBOX_UI=true`) for the first release so we can
  ship the resolver/API plumbing without exposing the UI to all
  users. Pull the flag once it's been exercised.
- **Tests:**
  - Resolver: session has stale id → fall through to list-for-owner
  - Resolver: session has other-owner's id → fall through (don't
    leak), and the bad id is dropped from session
  - Resolver: deterministic fallback ordering across multiple
    "default" sandboxes (multi-worker race aftermath)
  - Activation: 409 when caller has live cash session
  - In-game routes use `game.sandbox_id`, not session active id,
    even when those differ
  - Settings overlay: economy_flag honors sandbox override; falls
    back to module global when absent
  - AI seed honors `wealth_multiplier` regardless of which seeding
    path runs (`ensure_ai_bankrolls_seeded`, first-write,
    fresh-seat fallback); existing pre-feature seeds untouched
  - Archive of active sandbox: caller gets switched to next per
    fallback ordering
  - Personality pool filter narrows seating query
  - **Staking-migration tests:** staking events created in
    sandbox A don't appear in sandbox B's repository queries;
    forgiveness / settlement flows scope correctly; tier
    degradation history is sandbox-local
- **Estimated effort:** ~2 weeks total. Tier 0 (~half day) +
  Tier 1 (~2-3 days) + knobs implementation + API + UI + tests.
  Half of that is the staking-migration audit and the migration
  itself; don't underestimate it.

## Open questions

- **Staking-table audit.** Which staking-related tables actually
  need `sandbox_id` added, and which can derive it via a join
  through `cash_sessions`? First implementation task: read
  `poker/repositories/stake_repository.py` end-to-end and
  enumerate every read/write path. The audit drives the
  migration shape.
- **Migration of existing staking rows.** Backfill from
  `cash_sessions.sandbox_id` covers most cases, but staking rows
  that predate the sandbox-scoping work or were created via
  paths that didn't carry sandbox context need a default
  assignment. "Owner's earliest live sandbox" is the safe
  fallback. Worth a sanity-check query before the migration.
- **Cross-sandbox dossier.** The "Track Record" view already
  aggregates with `sandbox_id=None`. Confirm this still feels right
  when an owner has 5 sandboxes — do they want a per-sandbox view,
  a unified view, or both? Defer until we see real usage.
- **Naming collisions.** Should sandbox names be unique per owner?
  Probably yes; a `UNIQUE (owner_id, name) WHERE archived_at IS
  NULL` partial index keeps the picker readable.
- **Settings drift.** If a setting is renamed or removed in code,
  what happens to sandboxes that store the old key? Treat unknown
  keys as ignored on read; log a warning. Old keys never need
  migration.
- **Active-sandbox visual signal.** A "Active: Hardcore" header
  chip is enough, or do we want stronger color-coding to prevent
  "I thought I was in Casual" mistakes? Iterate after Phase 1.
