---
purpose: T2-42/T3-67 audit — zone gravity is dead code; recommendation is delete
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# Zone gravity — delete dead code (T2-42 / T3-67)

## Design intent

Zone gravity was designed as a third movement force alongside event pushes and recovery pull. Sweet spots exert a weak pull toward their center (magnitude `GRAVITY_STRENGTH × zone_strength`), making zones sticky. Penalty zones pull toward their extreme edge, making tilt harder to escape. Designed with `GRAVITY_STRENGTH = 0.03` (tunable 0.02–0.05). Source: `docs/technical/PSYCHOLOGY_ZONES_MODEL.md` §"Zone Gravity Force".

## Git history

No commit ever landed the gravity compute step. The 2026-02-06 "✅ implemented" date in the doc corresponds to zone detection scaffolding, not gravity computation. Evidence — gravity is dead across three layers:

1. **`poker/zone_config.py:55-57`** — `GRAVITY_STRENGTH = 0.03` stored under a `'gravity'` category key.
2. **`poker/zone_config.py:104`** — `get_zone_param()` searches only `['penalty_thresholds', 'zone_radii', 'recovery', 'energy_thresholds']`. The `'gravity'` key is excluded. Any call to `get_zone_param('GRAVITY_STRENGTH')` raises `KeyError`.
3. **`poker/player_psychology.py:651-658`** — `recover()` returns six keys (`recovery_conf`, `recovery_comp`, `recovery_energy`, `conf_after`, `comp_after`, `energy_after`). No gravity keys.
4. **`poker/psychology_pipeline.py:476-489`** — reads `recovery_info.get('gravity_conf', 0)` → always 0 → `if abs(...) > 0.001` guard always false → `save_event('_gravity', ...)` never called.

The two experiment configs `zone_tuning_pressure_WITH_gravity.json` and `zone_tuning_pressure_NO_gravity.json` ran identical workloads since gravity was 0 in both cases. Any prior comparison conclusions are invalid.

Zone detection infrastructure is fully present: `_detect_sweet_spots`, `_detect_penalty_zones`, `ZONE_*_CENTER` constants in `poker/zone_detection.py`. The compute step connecting detection to `recover()` was never written.

## Recommendation — DELETE

**Gravity adds no behavioral richness at this magnitude.** Maximum gravity force per hand: `0.03 × 1.0 = 0.030`. The smallest event impact is `win → +0.02 × sensitivity_floor(0.30) = 0.006`. Gravity is equivalent to 1–5 `win` events of push per hand — detectable but not architecturally significant.

**Tuning is already calibrated without gravity.** Every experiment since 2026-02-06 tuned event magnitudes, recovery floors, and penalty thresholds against a system with `gravity = 0`. Enabling gravity now invalidates those baselines and requires a full re-tuning campaign.

**Implementation cost exceeds benefit.** Correct gravity requires wiring `_detect_sweet_spots` / `_detect_penalty_zones` into `recover()`, computing per-axis deltas using `ZONE_*_CENTER` constants and a new penalty directions dict, fixing `get_zone_param` to include `'gravity'`, and re-running validation. Estimated: 2–3 days + experiment cost.

**Cleanup is ~10 lines with zero behavioral impact.** The dead code path was already unreachable.

## Cleanup steps

### 1. `poker/psychology_pipeline.py` — remove lines 475–488

Delete the `# Zone gravity force` comment and the 13-line block that reads `gravity_conf`/`gravity_comp` and calls `save_event('_gravity', ...)`. The `_recovery` save above is unaffected.

Update `_apply_recovery` docstring: remove "and persist recovery/gravity events" → "and persist recovery events".

### 2. `poker/zone_config.py` — remove lines 55–57

Delete the `'gravity': {'GRAVITY_STRENGTH': 0.03}` entry from the `defaults` dict in `_load_zone_params()`. No code calls `get_zone_param('GRAVITY_STRENGTH')` anywhere in the repo (confirmed by grep — zero results outside docs and this config).

### 3. `docs/technical/PSYCHOLOGY_ZONES_MODEL.md`

- Line 15: `✅ Zone gravity (stickiness) - implemented 2026-02-06` → `❌ Zone gravity (stickiness) - scaffolded but not implemented (see docs/triage/ZONE_GRAVITY_DECISION.md)`
- Remove `GRAVITY_STRENGTH` from the "Constants to Tune" table.
- Remove "Gravity strength tuning — 0.03 validated" from "Resolved Through Experiments" (those experiments ran without gravity active).
- Update `last_updated: 2026-05-15`.

### 4. `docs/technical/PSYCHOLOGY_OVERVIEW.md`

- Remove `GRAVITY_STRENGTH (0.03)` from the Recovery Constants row (~line 299).
- Remove `+ gravity_force` from the combined movement formula (~line 276).
- Update `last_updated: 2026-05-15`.

### 5. Experiment configs (optional, low priority)

Add `"note": "gravity was not implemented when designed; with/without comparison is invalid"` to `zone_tuning_pressure_WITH_gravity.json` and `zone_tuning_pressure_NO_gravity.json`.

## Test plan

Run the existing psychology suite — no changes required:

```bash
python3 scripts/test.py test_psychology
```

`tests/test_psychology_v2.py:1768` already assumes no gravity ("Zone gravity doesn't apply because (0.40, ~0.568) is neutral territory"). Zero test changes needed.

Before closing, verify no `_gravity` DB rows exist:

```sql
SELECT COUNT(*) FROM pressure_events WHERE event_type = '_gravity';
```

Expected: 0. If nonzero, investigate before deleting.

## Risks

**Deleting:** None. The deleted code path was already unreachable — the guard at `psychology_pipeline.py:478` never passed.

**If gravity is revisited:** Zone detection infrastructure is complete. A future implementation needs ~50 lines in `recover()` plus the `get_zone_param` category fix and a new penalty directions dict. Re-open this triage item rather than implementing now.
