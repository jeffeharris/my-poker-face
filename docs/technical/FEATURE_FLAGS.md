---
purpose: Architecture of the central feature-flag registry — lifecycle stages, per-env defaults, resolution pipeline, and how it integrates with config/DB/tests
type: architecture
created: 2026-06-07
last_updated: 2026-06-07
---

# Feature Flag System

The how-to (declare / read / graduate / inspect) lives in
[`docs/guides/FEATURE_FLAGS.md`](../guides/FEATURE_FLAGS.md). This document
describes the **system**: its structure, the resolution pipeline, and how it
plugs into the rest of the app.

## Why it exists

Boolean toggles used to be scattered `os.environ.get(...)` / `_env_flag(...)`
reads with inline defaults across `cash_mode/`, `flask_app/`, `poker/`, and
`core/`. Three failure modes followed:

1. **Lifecycle.** A feature shipped behind a flag, baked on a branch, then the
   flag was carried forever — re-armed by hand on each new branch, its dead
   `if flag:` branches never removed.
2. **Environment drift.** A fresh dev env meant remembering which flags to set in
   `.env`; nobody could state with confidence what was on where. (Concretely: at
   the time of writing, prod ran the 5-flag "Director thermostat" that dev did
   not — invisible until someone SSHed in and read the container env.)
3. **Prod visibility.** No single place answered "is every launched feature
   actually enabled on prod?"

The registry makes each flag a declared object with a lifecycle stage and
per-environment defaults, resolved through one pipeline that reports *where each
value came from*.

## Components

| Piece | Location | Role |
|---|---|---|
| Registry + resolver | `core/feature_flags.py` | The source of truth: `FeatureFlag` declarations, `Stage`, `resolve()`, `is_enabled()`, `snapshot()` |
| Back-compat binding | `cash_mode/economy_flags.py` | Module globals (`CHIP_CUSTODY_ENABLED`, …) bound from the registry at import, so the ~80 existing importers are unchanged |
| DB override store | `app_settings` table via `poker.repositories.SettingsRepository` | Optional runtime override; the same table the LLM model tiers use |
| Env detection | `current_env()` | Mirrors `poker.config.is_development_mode` (`FLASK_ENV` / `FLASK_DEBUG`) → `'dev'` / `'prod'` |
| CLI | `scripts/flags.py` | `status` board, `check` lifecycle report, `env` `.env.example` generator |
| Guards | `tests/test_feature_flags.py`, `tests/test_economy_flag_defaults.py` | Centralization + partition invariants |

## The `FeatureFlag` model

```python
@dataclass(frozen=True)
class FeatureFlag:
    name: str
    stage: Stage
    description: str          # one line; full rationale stays at the call site
    owner: str = ""           # groups the status report, e.g. "cash_mode.economy"
    dev: bool = False         # default when current_env() == 'dev'
    prod: bool = False        # default when current_env() == 'prod'
    db_overridable: bool = False
    since: str = ""
```

`dev` / `prod` are the per-environment defaults. They are *explicit* rather than
derived from the stage — that is what lets the registry represent real drift
(e.g. a flag `dev=False, prod=True`). Integrity rules (below) keep them
consistent with the stage.

## Lifecycle stages

```
EXPERIMENTAL ──promote──▶ BETA ──promote──▶ STABLE ──lock in──▶ GRADUATED
     │                                          │
     └────────────────── abandon ──────────────┴──────────────▶ RETIRED
```

| Stage | Default | Override? | Meaning |
|---|---|---|---|
| `EXPERIMENTAL` | off (`prod=False`) | env / DB | Freshly introduced, not validated; opt-in per deploy. |
| `BETA` | on in dev, off in prod | env / DB | Baking on a branch — fresh dev env gets it; prod untouched. |
| `STABLE` | on in prod (`prod=True`; dev may lag) | env / DB (kill switch) | A live, shipped feature. |
| `GRADUATED` | **locked on** | none | Permanent; the flag is no longer a toggle. Pending code cleanup. |
| `RETIRED` | **locked off** | none | Feature removed. Pending code cleanup. |

`GRADUATED` / `RETIRED` are the "lock in" states: their value is fixed in code and
**cannot** be changed by env or DB. That is what stops a finished feature from
being carried as a live toggle.

## Resolution pipeline

`resolve(flag, env=None) -> (value, source)`:

```
1. stage == GRADUATED        → (True,  "locked:graduated")   # short-circuit
2. stage == RETIRED          → (False, "locked:retired")     # short-circuit
3. env var <NAME> set        → (parsed, "env")               # 1/true/yes/on, 0/false/no/off
4. db_overridable + app_settings row set → (parsed, "db")
5. otherwise                 → (flag.default_for(env), "default:<env>")
```

The `source` string is surfaced by `scripts/flags.py status` so it is always
obvious *why* a flag holds its value (locked / env / db / per-env default).

Notes:
- Env detection (`current_env()`) reads `FLASK_ENV`/`FLASK_DEBUG`. On prod the
  compose sets `FLASK_ENV=production`, so `current_env() == 'prod'` and the
  `prod` defaults apply. This is load-bearing: it is why the prod env-var arming
  could be removed without flags flipping off.
- DB lookups are **lazy and best-effort**: `core/` imports `poker/` only inside
  the function body, and only flags that opt into `db_overridable` ever touch the
  DB — so the common env/default-only path pays no DB or schema cost.

## Back-compat binding (`economy_flags.py`)

The ~80 existing importers read module globals like
`economy_flags.CHIP_CUSTODY_ENABLED`. Those globals are now assigned from the
registry at import:

```python
from core.feature_flags import is_enabled as _flag
CHIP_CUSTODY_ENABLED: bool = _flag("CHIP_CUSTODY_ENABLED")
```

A module global is a **snapshot bound at import**; `is_enabled()` is evaluated
**live**. They agree unless the environment changes after import (e.g. the test
suite's reset fixture mutates globals without touching `os.environ`). New code
should call `is_enabled(...)` directly when it needs to observe a DB override at
runtime.

## DB overrides

For `db_overridable=True` flags, step 4 reads the `app_settings` table through
`SettingsRepository` — the **same table and pattern the LLM model tiers use**
(`core/llm/settings.py`). This means a flag can be flipped at runtime from the
admin Settings UI without a redeploy, if and when a flag opts in. Today the
economy flags do not opt in (env/default only), keeping import fast; the seam
exists for flags that want live toggling.

## Test architecture

Two invariants protect the system from regressing into the old sprawl:

1. **Centralization guard** (`test_feature_flags.py`): walks every `.py` file and
   fails if a registered flag name is read via `os.environ.get` / `os.getenv` /
   `_env_flag` anywhere outside the registry and the back-compat module. A new
   flag *must* go through the registry.

2. **Partition invariant** (`test_economy_flag_defaults.py`): the conftest test
   baseline forces economy flags to a deterministic value so an armed `.env`
   can't pollute the suite. Every non-locked economy flag must appear in exactly
   one of `RESET_ECONOMY_FLAGS` (forced off) or `TEST_BASELINE_ON_ECONOMY_FLAGS`
   (left on) — so a new flag can never silently slip the baseline. This baseline
   is intentionally decoupled from a flag's production stage (a `STABLE`, prod-on
   flag can still be forced off in tests where the simpler path is wanted).

`test_feature_flags.py` also checks stage/default consistency (e.g. `STABLE` ⇒
`prod=True`, `EXPERIMENTAL` ⇒ `prod=False`, locked stages ⇒ fixed value).

## Deployment relationship

Per-env defaults are set to match the **verified live state** of each
environment, so the registry — not scattered compose/`.env` arming — is the
source of truth:

- `docker-compose.prod.yml` no longer arms the launch flags; on prod
  (`env=prod`) they resolve on via their `prod=True` defaults. `GENESIS_RESERVE_RATIO`
  (a numeric tunable, not a boolean flag) is still set there.
- The dev `.env` and dev `docker-compose.yml` no longer arm them either; on dev
  (`env=dev`) they resolve from the `dev` defaults.

Verify any environment with one command (no SSH spelunking required):

```bash
python3 scripts/flags.py status            # current process env
python3 scripts/flags.py status --env prod # preview prod resolution
```

## Current inventory (2026-06-07)

All registered flags are `owner="cash_mode.economy"`.

- **GRADUATED** (locked on): `RAKE_ENABLED`, `PRESENCE_AUTHORITY_ENABLED`
- **RETIRED** (locked off): `PRESENCE_SHADOW_WRITE_ENABLED`
- **STABLE, dev+prod on**: `SIDE_HUSTLE_ENABLED`, `RAKE_PLAYER_TABLES`,
  `REPUTATION_DEMEANOR_ENABLED`, `DOSSIER_SCOUTING_GATE_ENABLED`,
  `CHIP_CUSTODY_ENABLED`, `CHIP_CUSTODY_DERIVE_READS`, `RENOWN_V2_ENABLED`,
  `RENOWN_V2_PERSIST_AI`, `PRESTIGE_SEEKING_ENABLED`, `TOURNAMENT_CIRCUIT_ENABLED`,
  `TOURNAMENT_DRAW_ENABLED`
- **STABLE, prod-only** (the Director thermostat — `dev=False, prod=True`):
  `GENESIS_RESERVE_ENABLED`, `RAKE_RESERVE_GATED`, `DIRECTOR_POLICY_HOLD`,
  `VICE_RESERVE_GATED`, `CASINO_RESEED_ON_SPENT`
- **EXPERIMENTAL** (off everywhere): `REGEN_ENABLED`, `DIRECTOR_INEQUALITY_RAKE`,
  `CASINO_RELATIVE_THRESHOLDS`, `TABLE_AFFINITY_ENABLED`

Net: **13/23 on in dev, 18/23 on in prod.**

## Out of scope

- **String/enum modes** (`VICE_MODE`, `LEVER_REFERENCE_MODE`) — not booleans.
- **LLM model tiers** — already DB-backed via `core/llm/settings.py` + admin UI.
- **React/Vite flags** (`VITE_ENABLE_*`) — build-time client flags, separate system.
