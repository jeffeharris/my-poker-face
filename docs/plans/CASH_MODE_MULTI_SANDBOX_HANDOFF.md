---
purpose: Extend the per-player sandbox foundation (Phase 2.5) to support multiple sandboxes per account as a power-user feature, each with its own starting conditions and tunable knobs, so players can run distinct cash-mode playthroughs without losing earlier worlds.
type: guide
created: 2026-05-24
last_updated: 2026-05-25
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
  guards on the resolver (per-request owner+live revalidation),
  deterministic fallback ordering, durable-`cash_sessions`
  activation block, fixing the knob-wiring hooks. Independent
  of any data-model change; should ship regardless.
- **Tier 1 — Staking scoping (~2-3 days).** Destructive migration
  on `stakes` and the related staking tables to add
  `sandbox_id`. Thread it through `stake_repository.py` and the
  Phase 3+ business logic. Same shape as the v109
  `cash_pair_stats` migration. **Must ship before the UI** —
  see "Sequencing inside Phase 1" below.
- **Tier 2 — Human-bankroll scoping (deferred, ~3-5 days if we
  ever do it).** Scope `player_bankroll_state` per sandbox. See
  "Deferred" below for the design rationale on skipping this.

Honest effort estimate: original draft suggested "~1 week MVP."
Real cost with staking scoping, route classification, and
`RakePolicy` extraction included is closer to **~2 weeks**, with
the audit pass being the bottleneck.

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
the call sites and call patterns the staking system touches;
some may already carry `sandbox_id` via `cash_sessions` joins
and only need a filter, not a column):

- **Tables / state:**
  - `stakes` (carry-relationship rows)
  - Stake-event / settlement tables under
    `poker/repositories/stake_repository.py`
  - Forgiveness ask / payoff / default resolution state
  - Net-worth history rows
  - Borrower-tier / lender-tier degradation state
  - Sponsor-offer queue (if persisted)
  - Any staking-derived view materialized into another table
- **Query patterns to scrub:**
  - Lookups by `borrower_id` — currently global, must filter on
    `sandbox_id` (or join through `cash_sessions`)
  - Lookups by `staker_id` — same
  - "Active stakes for session" — should be sandbox-implicit via
    the session, but verify the join
  - Carry-list queries (whose carries are live?)
  - Sponsor-offer filtering (which AIs are eligible to back this
    player?)
  - Stakable-AI lookup (which AIs accept backing right now?)
  - Player-as-staker candidate validation
  - AI payoff / forgiveness / default resolution paths

**Repo-signature rule:** after the migration, any
`stake_repository` method whose key is not the immutable
`stake_id` must take `sandbox_id` as a required parameter — not
optional, not derived inside. This prevents future callers from
"forgetting" sandbox context and silently leaking. For
`load_stake(stake_id)` (and similar single-row by-id reads),
the caller is responsible for owner+sandbox authorization
*before* acting on the result; the repo cannot enforce that
because the caller may legitimately be the audit layer.

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

Four knobs, all backed by mechanisms that already exist as globals
or per-personality fields. `rng_seed` was demoted to Phase 2
during the second review pass — see Phase 2 for why.

| Knob | Effect | Backed by | Default |
|---|---|---|---|
| `wealth_multiplier` | Scales every personality's `starting_bankroll` and `bankroll_rate` on first AI seed in this sandbox | `personalities.config_json.bankroll_knobs`, applied via a sandbox-aware overlay on `load_personality_knobs(...)` so every seeding path (`ensure_ai_bankrolls_seeded`, `save_ai_bankroll` first-write, fresh-seat fallback) gets the multiplier consistently | `1.0` |
| `default_bot_type` | Sandbox-wide default for AI controller (`chaos` / `standard` / `lean` / `sharp` / `baseline_solver` / `casebot`); per-game override still wins | `flask_app/routes/game_routes.py` bot_type dispatch | `standard` |
| `economy_flags` | Per-sandbox override for `REGEN_ENABLED`, `RAKE_ENABLED`, `RAKE_PLAYER_TABLES`, `RAKE_RATE`, `RAKE_CAP_BB` — but **wired through two different paths** (see "Economy-flag wiring" below) | `cash_mode/economy_flags.py` module globals, projected via per-personality knobs OR through a `RakePolicy` at the hand boundary depending on the flag | inherit current globals |
| `personality_pool` | Optional allowlist of personality IDs (or tier labels) eligible to spawn in this sandbox's lobby | `cash_mode/seating.py` query | `None` (all eligible) |

These four already give "playthroughs feel distinct" without
touching any of the harder-to-decouple subsystems.

### Economy-flag wiring (the coupling problem)

Not all economy flags are wired the same way, and a flat
`economy_flag(name, sandbox_id)` helper would actively break
the rake path. Split them:

- **Projection flags** (`REGEN_ENABLED`, future regen-rate
  variants) — affect AI bankroll projection over time. These
  *can* be baked into the per-personality knobs at load time so
  `project_bankroll(...)` and similar callers don't need
  sandbox threading. Overlay-at-the-source via
  `load_personality_knobs(...)`, same seam as
  `wealth_multiplier`.
- **Hand-boundary flags** (`RAKE_ENABLED`, `RAKE_RATE`,
  `RAKE_CAP_BB`, `RAKE_PLAYER_TABLES`) — table/hand/action-time
  behavior. `compute_rake(pot, big_blind)` runs at hand
  resolution and needs a *policy* input, not a flat flag
  lookup. Introduce a `RakePolicy` dataclass passed at the hand
  boundary, resolved from sandbox settings (and, eventually,
  per-table type — lobby vs casino, human vs AI table). Even if
  the policy is just a snapshot of the four flags in v1, having
  it as a parameter at the hand boundary leaves the door open
  for table-type variation without another refactor.
- **Why this matters:** "bake everything into knobs" was the
  shortcut suggested in the first revision, but it breaks
  rake-per-table immediately. Doing the split now adds maybe
  half a day; doing it later means revisiting every rake call
  site.

## Phase 2 (after Phase 1 lands and gets used)

These are higher-value but need real engineering. Worth doing only
after we see which knobs Phase 1 users actually reach for.

- **`rng_seed`** (demoted from Phase 1 during second review). The
  intent was "set a sandbox seed → reproducible scenarios", but
  the code has at least seven independent random streams (lobby
  seeding, full-sim hand dealing, sponsor sampling, vice rolls,
  casino spawning, movement/pressure, controller randomness).
  Wiring one master seed through all of them is not a small
  refactor, and a partial wiring is worse than nothing (false
  promise of reproducibility). Two paths in Phase 2:
  - **Narrow option**: seed only the lobby/seating stream
    (`cash_mode/seating.py` + table assignment). Honest about
    what's reproducible. Maybe a day.
  - **Full option**: thread a `SandboxRng` factory through every
    random consumer in cash mode. Several days.
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
- **Per-table-type `RakePolicy`** — once the `RakePolicy` dataclass
  exists (Phase 1), let it vary by table type (lobby vs casino,
  human vs AI table). Cheap extension once the seam is in place.

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
own `sandbox_id` in the `cash_sessions` table. If the user
switches active sandbox while a cash session is live, lobby
refresh / stake settlement / chip ledger writes could resolve a
different sandbox than the game's. The guard rule:

- `POST /api/sandboxes/<id>/activate` queries `cash_sessions`
  (the durable row, **not** in-memory game state — restart
  recovery paths exist) for any non-terminal session owned by
  the caller. Returns 409 if found. They must leave the table
  first.

### Route classification (sandbox source per cash-mode route)

A binary "in-game uses game.sandbox_id, lobby uses session" rule
is too coarse. Cash-mode routes touch sandbox state in at least
three distinct ways, and the wrong default in any class is a
correctness bug. Classify each route into one of:

- **Class A — Bound to active session/game** (sandbox comes
  from the `cash_sessions` / `games` row): in-game actions
  (bet/call/fold), mid-session forgiveness asks, payoff /
  default / carry resolution, stake settlement, mid-hand chip
  ledger writes. Even if the user has switched their session
  active sandbox to something else, these routes must stay
  pinned to the row they were dispatched from.
- **Class B — Bound to session-active sandbox** (sandbox comes
  from `session['active_sandbox_id']` via the resolver): lobby
  list, lobby refresh, sponsor offers, stakable-AI candidate
  list, net-worth dashboard for the active sandbox, casino
  spawn/provisioning that targets the active world.
- **Class C — Requires explicit `sandbox_id` param +
  ownership check**: admin views ("show me sandbox X's
  dossier"), cross-sandbox audit views, `PATCH /api/sandboxes/<id>`,
  `POST /api/sandboxes/<id>/archive`, the
  `sandbox_id=None`-aggregate Track Record view. These take the
  id from the URL/body and verify caller ownership; they must
  not fall back to session-active resolution because the user
  may legitimately be acting on a non-active sandbox.

**Implementation rule:** the audit pass that produces the
staking-table list (above) also produces this route
classification. Each cash-mode route gets tagged A / B / C and
its handler reads from the appropriate source. The default
resolver helper `_resolve_sandbox_id(owner_id)` only serves
Class B; routes in A and C must not call it.

All existing cash routes today implicitly resolve to Class B
because there's only one sandbox per owner. The audit reveals
which of them are actually Class A (a real correctness bug
waiting to happen as soon as multi-sandbox ships) and which are
Class C (rare today, but archive/activate are already this
shape).

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
  - Activation guard: 409 when caller has a non-terminal
    `cash_sessions` row, even if in-memory game state is empty
    (simulates post-restart recovery case)
  - Class A routes use the game's `sandbox_id` from `cash_sessions`
    / `games`, not session active id, even when those differ
    (mid-session activate-then-act scenario)
  - Class C routes (admin, archive, PATCH) honor explicit
    `sandbox_id` param + ownership check; reject cross-owner
    access; do not fall through to session-active resolution
  - Settings overlay: projection-flag overrides land in
    `load_personality_knobs` output; defaults inherited when
    sandbox setting is absent
  - `RakePolicy` resolution: sandbox flag overrides reflected in
    `compute_rake()` output; default behavior preserved when
    sandbox has no override
  - AI seed honors `wealth_multiplier` regardless of which seeding
    path runs (`ensure_ai_bankrolls_seeded`, first-write,
    fresh-seat fallback); existing pre-feature seeds untouched
  - Archive of active sandbox: caller gets switched to next per
    fallback ordering
  - Personality pool filter narrows seating query
  - **Staking-migration tests:** staking events created in
    sandbox A don't appear in sandbox B's repository queries;
    forgiveness / settlement / payoff / default flows scope
    correctly; tier degradation history is sandbox-local; sponsor
    offer filtering scoped; player-as-staker validation scoped;
    repo signature rule enforced (non-`stake_id` reads reject
    missing `sandbox_id`)
- **Estimated effort:** ~2 weeks total. Tier 0 (~half day) +
  Tier 1 (~2-3 days) + `RakePolicy` extraction (~half day) +
  knobs implementation + API + UI + tests. Roughly half of that
  is the staking-migration audit and the migration itself;
  don't underestimate it.

### Sequencing inside Phase 1 (do in this order)

The second review pass flagged a real failure mode: ship the UI
first and the user gets a visibly working sandbox picker while
staking, carries, and net-worth bleed silently across worlds.
Order matters:

1. **Audit pass** — read `stake_repository.py` and adjacent code
   end-to-end. Produce two artifacts: the staking-table column
   list and the cash-mode route Class A/B/C classification.
   Nothing else can be sized correctly until this is done.
2. **Staking migration (Tier 1)** — destructive migration with
   backfill, repo signatures updated to require `sandbox_id` on
   non-`stake_id` reads. Tests for cross-sandbox isolation.
3. **Resolver hardening (Tier 0)** — per-request revalidation,
   deterministic fallback ordering, durable-session activation
   guard, Class A/B/C route refactors. Tests for auth-leak and
   pin-to-game-sandbox cases.
4. **`RakePolicy` extraction** — pull rake flag reads out of
   `economy_flags` module globals and into a policy passed at
   hand boundary. No behavior change yet; sandbox just resolves
   to current globals.
5. **Settings storage** — add `settings_json` column to
   `sandboxes`, `SandboxSettings` dataclass, overlay seams on
   `load_personality_knobs` and `RakePolicy` resolution.
6. **Individual knobs** — wire `wealth_multiplier`,
   `default_bot_type`, `economy_flags`, `personality_pool`. Each
   is small once the seams from step 5 exist.
7. **API surface** — `GET/POST/PATCH /api/sandboxes`,
   `activate`, `archive`. Behind feature flag.
8. **UI** — `/sandboxes` admin page, header chip, presets.
   Behind feature flag.

Steps 1-4 are correctness-critical and ship invisibly. Steps
5-8 are user-facing and gated on the feature flag.

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
