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
owner — every scoped table (`ai_bankroll_state`, `cash_tables`,
`cash_pair_stats`, `ai_vice_state`, `cash_idle_pool`) has
`sandbox_id` in its primary key, and `SandboxRepository.list_for_owner`
already returns a list. The only chokepoint is
`flask_app/services/sandbox_resolver.py` which always returns the
first live sandbox (or creates one).

This work opens that chokepoint and exposes a small set of
per-sandbox knobs so players can run **distinct playthroughs**
("Casual seed", "Hardcore rake-on, regen-off", "Sharp-bots-only,
high-roller stakes") side-by-side without one bleeding into the
other.

## Goals

- A single account can own N live sandboxes, each with its own
  bankrolls / tables / pair-stats / vice state (data model already
  supports this).
- Each sandbox carries a small bag of **starting-condition
  overrides** (wealth multiplier, bot-type defaults, economy flags,
  personality pool, RNG seed) stored as opaque JSON on the
  `sandboxes` row.
- Players can list, create, switch, and archive their own
  sandboxes via a simple admin-style page.
- The active sandbox for a request is resolved from session state
  with a sensible fallback chain, so existing single-sandbox users
  see zero behavior change.

## Non-goals

- **Not a polished, in-game UX.** This is a power-user feature.
  A hidden `/sandboxes` admin page with a JSON editor and a few
  presets is enough for v1. New players never encounter it; the
  default sandbox flow stays identical.
- **Not changing the staking economy phasing.** Multi-sandbox
  scoping happens beneath the backing system; Phase 3+ work
  continues as planned.
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

## The knobs — Phase 1 MVP

Five knobs, all backed by mechanisms that already exist as globals
or per-personality fields. Each one is a small refactor to read
from sandbox context instead of a module constant.

| Knob | Effect | Backed by | Default |
|---|---|---|---|
| `wealth_multiplier` | Scales every personality's `starting_bankroll` and `bankroll_rate` on first AI seed in this sandbox | `personalities.config_json.bankroll_knobs` (`cash_mode/bankroll.py`) | `1.0` |
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

## Resolver / selection changes

`flask_app/services/sandbox_resolver.py` today:

```
resolve_default_sandbox_for(owner_id):
  cache → list_for_owner → create
```

After this work:

```
resolve_active_sandbox_for(owner_id, session):
  1. session['active_sandbox_id'] if set and still owned + live
  2. list_for_owner(owner_id) first row
  3. create + return
```

The cache layer stays — keyed on `(owner_id, sandbox_id)` rather
than just `owner_id`. Cache invalidation on archive/create.

All existing cash routes already call
`_resolve_sandbox_id(owner_id)`; they just point at the new
function. No route signature changes.

Where settings get consumed:

- `cash_mode/bankroll.py` — `record_ai_seed()` reads
  `sandbox_settings.wealth_multiplier` and scales the personality's
  knobs before persisting.
- `flask_app/routes/game_routes.py` — bot_type dispatch falls back
  to `sandbox_settings.default_bot_type` if the request doesn't
  specify.
- `cash_mode/economy_flags.py` — replace direct module-global reads
  with `economy_flag(name, sandbox_id)` helper that overlays
  sandbox overrides on top of module defaults.
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

- Schema: add `settings_json TEXT` column to `sandboxes`. Existing
  rows get `NULL`, which the resolver treats as "all defaults" —
  zero behavior change for current users.
- Backfill: none required.
- Feature flag: gate the `/sandboxes` page behind an env var
  (`ENABLE_MULTI_SANDBOX_UI=true`) for the first release so we can
  ship the resolver/API plumbing without exposing the UI to all
  users. Pull the flag once it's been exercised.
- Tests:
  - Resolver: session has stale id → fall through to list-for-owner
  - Resolver: session has other-owner's id → fall through (don't
    leak)
  - Settings overlay: economy_flag honors sandbox override; falls
    back to module global when absent
  - AI seed honors `wealth_multiplier`; existing seeds untouched
  - Archive of active sandbox: caller gets switched to next-newest
  - Personality pool filter narrows seating query

## Open questions

- **Naming collisions.** Should sandbox names be unique per owner?
  Probably yes; a `UNIQUE (owner_id, name) WHERE archived_at IS
  NULL` partial index keeps the picker readable.
- **Cross-sandbox dossier.** The "Track Record" view already
  aggregates with `sandbox_id=None`. Confirm this still feels right
  when an owner has 5 sandboxes — do they want a per-sandbox view,
  a unified view, or both? Defer until we see real usage.
- **Settings drift.** If a setting is renamed or removed in code,
  what happens to sandboxes that store the old key? Treat unknown
  keys as ignored on read; log a warning. Old keys never need
  migration.
- **Active-sandbox visual signal.** A "Active: Hardcore" header
  chip is enough, or do we want stronger color-coding to prevent
  "I thought I was in Casual" mistakes? Iterate after Phase 1.
