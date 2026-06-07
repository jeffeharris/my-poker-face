---
purpose: How to declare, read, resolve, and retire feature flags via the central registry
type: guide
created: 2026-06-07
last_updated: 2026-06-07
---

# Feature Flags

All boolean feature toggles are declared in one place — the registry in
[`core/feature_flags.py`](../../core/feature_flags.py) — and read through it.
This replaced the old pattern of scattered `os.environ.get(...)` / `_env_flag(...)`
reads with inline defaults, which made three things painful: carrying flags
forever (re-enabling them by hand on every branch), env drift (nobody could say
what was on in a fresh dev env), and prod visibility (no single place to confirm
launched features were actually enabled).

## The model

A flag is a `FeatureFlag` with a **lifecycle stage** and a **per-environment
default**:

| Stage | Default behaviour | Env / DB override? | Meaning |
|---|---|---|---|
| `EXPERIMENTAL` | off everywhere | yes | Freshly introduced; opt-in per deploy. Not validated. |
| `BETA` | on in dev, off in prod | yes | Baking on a branch — a fresh dev env gets it; prod stays untouched. |
| `STABLE` | on everywhere | yes (kill switch retained) | A live, shipped feature. |
| `GRADUATED` | **locked on** | no | Permanent. The flag is no longer a toggle — delete it and its dead branches. |
| `RETIRED` | **locked off** | no | The feature was removed — delete it and its dead branches. |

### Resolution order

For a non-locked flag, `is_enabled(name)` resolves in this order (and the status
board reports which one won):

1. An **environment variable** of the same name, if set (`1/true/yes/on`).
2. A **DB `app_settings` row**, if the flag declared `db_overridable=True`
   (reuses the same table the LLM model tiers use — so admin-UI runtime toggling
   is available where wanted).
3. The flag's **per-environment default** (`dev`/`prod`), chosen by `FLASK_ENV`
   (`development`/`FLASK_DEBUG=1` → `dev`, otherwise `prod`).

`GRADUATED` / `RETIRED` flags short-circuit to their locked value before step 1 —
that is what "locked in" means.

## Reading a flag

```python
from core.feature_flags import is_enabled

if is_enabled("TOURNAMENT_DRAW_ENABLED"):
    ...
```

In `cash_mode/economy_flags.py` the historical module globals
(`economy_flags.CHIP_CUSTODY_ENABLED`, etc.) are still exported for the ~80
existing importers — their value is now sourced from the registry at import time.
New code should prefer `is_enabled(...)` so it picks up DB overrides live.

> **Note:** a module global is a snapshot bound at import; `is_enabled()` is
> evaluated live. They agree unless something changes the env/DB after import
> (e.g. the test suite's reset fixture). Prefer `is_enabled()` when that matters.

## Adding a flag

Declare it in `core/feature_flags.py`:

```python
register(FeatureFlag(
    "MY_FEATURE_ENABLED", Stage.EXPERIMENTAL,
    "One-line description of what it gates.",
    owner="cash_mode.economy", dev=False, prod=False,
))
```

Do **not** add a new `os.environ.get("MY_FEATURE_ENABLED")` anywhere else —
`tests/test_feature_flags.py::test_flags_are_only_read_through_the_registry`
fails if a registered flag is read via a raw env construct outside the registry.

If the flag is an EXPERIMENTAL **economy** flag, also add it to
`tests/conftest.py::RESET_ECONOMY_FLAGS` (the drift guard
`test_economy_flag_defaults.py` enforces this), so a developer's armed `.env`
can't pollute the test baseline.

## Seeing what's on (the board)

```bash
# Resolved board for the current process's env, with the source of each value:
python3 scripts/flags.py status

# Preview how flags resolve in another env (defaults only — env/DB of THIS
# process still apply as overrides):
python3 scripts/flags.py status --env prod
```

To confirm prod state, run it inside the prod backend:

```bash
ssh root@<prod> "cd /opt/poker && \
  docker compose -f docker-compose.prod.yml exec -T backend \
  python3 scripts/flags.py status --env prod"
```

## Locking a flag in (graduation) and cleanup

When a feature is locked in, change its stage to `GRADUATED` (or `RETIRED` if it
was abandoned). It is then env-locked and stops being something you manage per
branch. The dead `if flag:` branches can be removed on your own schedule —
`scripts/flags.py check` lists graduated/retired flags whose code still
references the name, plus any `BETA` flags that have been lingering:

```bash
python3 scripts/flags.py check
```

## Per-env defaults reflect what's actually deployed

The registry defaults were set to match the **verified live state** of dev and
prod (read from the dev `.env` and the running prod container, 2026-06-07):

- **On in dev + prod** (`STABLE`, `dev=True, prod=True`): `CHIP_CUSTODY_ENABLED`,
  `CHIP_CUSTODY_DERIVE_READS`, `RENOWN_V2_ENABLED`, `RENOWN_V2_PERSIST_AI`,
  `PRESTIGE_SEEKING_ENABLED`, `TOURNAMENT_CIRCUIT_ENABLED`,
  `TOURNAMENT_DRAW_ENABLED` (plus the always-on `SIDE_HUSTLE_ENABLED`,
  `RAKE_PLAYER_TABLES`, `REPUTATION_DEMEANOR_ENABLED`,
  `DOSSIER_SCOUTING_GATE_ENABLED`).
- **Prod only — the Director thermostat** (`STABLE`, `dev=False, prod=True`):
  `GENESIS_RESERVE_ENABLED`, `RAKE_RESERVE_GATED`, `DIRECTOR_POLICY_HOLD`,
  `VICE_RESERVE_GATED`, `CASINO_RESEED_ON_SPENT`. The `dev=False/prod=True`
  split makes this real drift visible: **dev does not run the prod economy.**
  Flip `dev=True` on these to close the drift and exercise the prod economy
  locally.
- **Locked**: `RAKE_ENABLED`, `PRESENCE_AUTHORITY_ENABLED` (`GRADUATED`),
  `PRESENCE_SHADOW_WRITE_ENABLED` (`RETIRED`).

Because the defaults now produce the correct value on their own
(`scripts/flags.py status --env prod` shows `source: default:prod`), the
`${X:-1}` flag lines in `docker-compose.prod.yml` and the flag lines in the dev
`.env` are **redundant** and can be removed — the registry is the source of
truth. (Do that as a follow-up once this lands and is deployed, so prod never
briefly loses a flag during the transition.)

Change stages/defaults deliberately by editing `core/feature_flags.py` — never by
flipping a value silently.

## Not yet migrated (backlog)

The registry currently owns the `cash_mode.economy` boolean flags. Still read
directly elsewhere (candidates for the next pass):

- **Flask config** (`flask_app/config.py`): `ENABLE_AI_DEBUG`,
  `ENABLE_AVATAR_GENERATION`, `ENABLE_AI_COMMENTARY`, `CSRF_PROTECTION_ENABLED`
  (the last is security-sensitive — migrate carefully).
- **Cross-module booleans**: `SARCASM_DETECTION_ENABLED`
  (`flask_app/handlers/chat_relationship.py`), `COMMENTARY_ENABLED`
  (`poker/config.py`).
- **String/enum modes** (not booleans, so out of scope for this registry):
  `VICE_MODE`, `LEVER_REFERENCE_MODE`.
- **LLM model tiers**: already DB-backed via `core/llm/settings.py` + the admin
  Settings UI — a separate, established mechanism; leave as-is.
- **React/Vite flags** (`VITE_ENABLE_DEBUG`, `VITE_ENABLE_AI_DEBUG`): build-time
  client flags, a different system.
