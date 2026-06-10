---
purpose: A promote / hold / kill recommendation for every feature flag, registry and non-registry, with rationale and concrete next actions
type: guide
created: 2026-06-10
last_updated: 2026-06-10
---

# Feature Flag Lifecycle Plan

A per-flag disposition for the **31 registry flags** + the **non-registry
backlog**. This is the "determine a plan" half of the flag-consolidation work;
the inventory and how-to live in
[`docs/guides/FEATURE_FLAGS.md`](../guides/FEATURE_FLAGS.md) and the system
architecture in [`docs/technical/FEATURE_FLAGS.md`](../technical/FEATURE_FLAGS.md).

Regenerate the live state any time with:

```bash
python3 scripts/flags.py status --env dev
python3 scripts/flags.py status --env prod
python3 scripts/flags.py check        # lifecycle nudges (dead branches, lingering betas)
```

## What the three verbs mean here

| Verb | Stage move | When |
|---|---|---|
| **Promote** | `EXPERIMENTAL→BETA→STABLE→GRADUATED`, or close a dev/prod default gap | The feature is validated at its current ring and ready for the next. |
| **Hold** | no change | Live and stable with the kill switch worth keeping, OR still under active R&D, OR blocked upstream. |
| **Kill** | `→RETIRED` (+ delete dead branches), or just delete already-locked flags' branches | The feature is superseded, abandoned, or permanently locked-in (graduated) and only the dead toggle code remains. |

Note: graduating a `STABLE` flag **removes its kill switch** — the value is then
locked in code (`locked:graduated`) and can no longer be disabled by env or DB.
That is a deliberate, one-way commitment. Don't graduate anything you might still
want to roll back.

---

## Registry — `cash_mode.economy`

### Kill (cleanup — locked flags whose dead branches remain)

`scripts/flags.py check` flags these: the toggle is locked but code still
branches on the name. Delete the `if flag:` branches, then the declaration.

| Flag | Stage | Action | Notes |
|---|---|---|---|
| `PRESENCE_SHADOW_WRITE_ENABLED` | RETIRED | ✅ **DONE (2026-06-10)** | Removed. NOT the clean delete it first appeared: the `presence_shadow` module is *live* (the off-grid mirror), kept alive by the graduated `PRESENCE_AUTHORITY_ENABLED` — only the retired flag operand was dead. Dropped the operand in `is_enabled()`, removed the dead `shadow` branch + try/except in `presence_transitions`, deleted the declaration + global, rewired the `test_shadow_*`/cutover tests to the authority flag, and deleted the two obsolete cutover-validation scripts (`validate_presence_shadow.py`, `audit_presence_divergence.py`). |
| `RAKE_ENABLED` | GRADUATED | **Do NOT kill — reclassify** | NOT a dead branch: `economy_flags.compute_rake` returns 0 when it's off, and the economy test suite (`TestUniverseConservation`, `test_no_rake_when_disabled`) uses it as a live sink on/off knob for the faucet-vs-sink conservation invariant. `flags.py check` lists it only because GRADUATED + referenced. **Inconsistency to resolve:** it's marked GRADUATED ("no longer a toggle") while the sim still toggles it — either un-graduate it (it *is* a real knob) or stop the tests toggling it. Leave the runtime as-is. |
| `PRESENCE_AUTHORITY_ENABLED` | GRADUATED | **Kill branches** | Permanent but ~20 refs across presence subsystem + tests. Larger cleanup; schedule deliberately, lowest urgency. |

### Kill (retire a superseded experiment)

| Flag | Stage | Action | Notes |
|---|---|---|---|
| `REGEN_ENABLED` | RETIRED | ✅ **DONE (2026-06-10)** | Retired — superseded by `SIDE_HUSTLE_ENABLED`. Stage flipped to RETIRED (locked off) and removed from the conftest reset set. The `bankroll.py:187` regen branch is left as dead-pending-cleanup (still exercised by economy tests that set the global directly, like `RAKE_ENABLED`); a follow-up can delete the mechanism + those test arms. |

### Promote (close the Director-thermostat dev/prod drift) — ✅ DONE (2026-06-10)

These five were `STABLE, dev=False, prod=True` — live in prod but off in dev, so
local runs didn't exercise the prod economy. **There was no principled reason for
the split** — the thermostat was built/tuned and shipped to prod, but dev `.env`s
were never armed; the registry just encoded that drift honestly. All five flipped
`dev=True`; dev now runs the same economy as prod. (Tests still force them off via
`RESET_ECONOMY_FLAGS`, so determinism is unaffected.)

| Flag | Status |
|---|---|
| `GENESIS_RESERVE_ENABLED` | ✅ dev=True |
| `RAKE_RESERVE_GATED` | ✅ dev=True (already flipped for the BETA inequality-rake eval) |
| `DIRECTOR_POLICY_HOLD` | ✅ dev=True |
| `VICE_RESERVE_GATED` | ✅ dev=True |
| `CASINO_RESEED_ON_SPENT` | ✅ dev=True |

> Recommended but **optional / your call**: keeping dev=False is a legitimate
> choice if you want dev to deliberately run the *simpler* (non-thermostat)
> economy. If so, change the recommendation to Hold and note it in the registry
> comment so the drift reads as intentional, not forgotten. Either way, make it a
> decision — don't let it sit ambiguous.

### Promote (graduation candidates — proven STABLE, consider locking in)

| Flag | Action | Rationale / caution |
|---|---|---|
| `CHIP_CUSTODY_ENABLED` | **Graduate candidate** | Ledger is the chip authority post-cutover; on in dev+prod and load-bearing. Graduating locks out rollback — only do it once you're certain you'll never run the pre-ledger path. Until then, Hold is safe. |
| `CHIP_CUSTODY_DERIVE_READS` | **Graduate candidate** | Paired with the above; graduate together or not at all. |

### Hold (live, shipped — keep the kill switch)

No change. These are `STABLE` and on; the retained kill switch is cheap insurance.

`SIDE_HUSTLE_ENABLED`, `RAKE_PLAYER_TABLES`, `REPUTATION_DEMEANOR_ENABLED`,
`DOSSIER_SCOUTING_GATE_ENABLED`, `RENOWN_V2_ENABLED`, `RENOWN_V2_PERSIST_AI`,
`PRESTIGE_SEEKING_ENABLED`, `TOURNAMENT_CIRCUIT_ENABLED`, `TOURNAMENT_DRAW_ENABLED`.

### Hold (experimental — active roadmap or undecided lever)

| Flag | Action | Rationale |
|---|---|---|
| `CAREER_PROGRESSION_ENABLED` | **Hold** | Act-1 narrative master gate, in active development (M1 built, M2-M4 pending). Correctly EXPERIMENTAL. |
| `CAREER_VOUCH_ENABLED` | **Hold** | Career-M2 vouches; same workstream. |
| `INTAKE_WORLD_WARMUP_ENABLED` | **Hold** | Inert without the career gate; part of the same workstream. |
| `DIRECTOR_INEQUALITY_RAKE` | **BETA, dev-on (2026-06-10)** | Promoted to BETA for dev evaluation (prod-off); `RAKE_RESERVE_GATED` flipped dev-on so it isn't a no-op. |
| `CASINO_RELATIVE_THRESHOLDS` | **BETA, dev-on (2026-06-10)** | Promoted to BETA for dev evaluation (prod-off). |
| `TABLE_AFFINITY_ENABLED` | **BETA, dev-on (2026-06-10)** | Promoted to BETA for dev evaluation (prod-off). Establishes "home tables" — an AI prefers the table it wins at most within whichever tier it can afford. |

**Dev-eval sim (2026-06-10, 600-tick A/B, isolated temp DB):** all three are
**conservation-safe** (`max_abs_audit_drift = 0` in both arms). Efficacy was
**not** established — the run left reserves FLUSH (`ratio ≈ 1.5` vs the `0.12`
trigger), so the reserve-gated rake stayed dormant and `DIRECTOR_INEQUALITY_RAKE`
never fired (it also needs a FLAT field, `p90/median ≤ 2.5`, but the field sat at
~3.0–3.6). `CASINO_RELATIVE_THRESHOLDS` only moves the spawn/close *pool-depth
gates* (scaled to holdings), **not** prefund amounts — casino count (3) and fish
(6) were identical across arms, so the earlier "~2.25× more casino funding" read
was a confound (different starting holdings + combined-flag run + single-seed RNG
desync), not a flag effect. A proper efficacy eval needs depleted reserves +
per-flag isolation + identical starts + a room-concentration metric for affinity.
Verdict: **safe to keep dev-on; not yet a prod candidate.**

---

## Registry — `poker.strategy` (tilt / excursion system)

All five are `db_overridable=True` and brand-new (added this branch; see
`TILT_EXCURSION_DESIGN.md`). **Upstream blocker:** per
[`EMOTIONAL_SYSTEM_ANALYSIS.md`](../technical/EMOTIONAL_SYSTEM_ANALYSIS.md)
(2026-06-09), tilt is *nearly unreachable* — composure is floored at ~0.40 (the
tilt line) by a baseline clamp + same-hand recovery, which starves every feature
that gates on `composure < 0.4`. **Promoting any tilt flag to prod before the
reachability fix lands would ship an effectively-inert feature.**

| Flag | Stage | Action | Rationale |
|---|---|---|---|
| `TILT_CONDITIONING_ENABLED` | BETA | **Hold (blocked)** | `check` flags it as a lingering beta — but the right move is *not* to promote yet. Hold pending the emotional-reachability fix, then promote or retire deliberately. Record the blocker so it doesn't read as neglect. |
| `TILT_PERSISTENCE_ENABLED` | EXPERIMENTAL | **Hold** | Active R&D; inert when off. Keep experimental. |
| `TILT_TELEGRAPH_ENABLED` | EXPERIMENTAL | **Hold** | Active R&D (telegraph just merged). Keep experimental. |
| `TILT_ERRATIC_READS_ENABLED` | EXPERIMENTAL | **Hold** | Active R&D; changes decisions. Keep experimental. |
| `TILT_SIGNATURE_ENABLED` | EXPERIMENTAL | **Hold** | Active R&D; changes decisions. Keep experimental. |

> These flags are the rare case where the right answer is genuinely "wait":
> they're correctly gated, but the system they act on needs an upstream fix
> before validation is even meaningful. The blocker is the work, not the flags.

---

## Non-registry flags

### Migrate into the registry (consolidation) — ✅ DONE (2026-06-10)

All 12 boolean toggles below were migrated into `core/feature_flags.py` (registry
30→42 flags) and now resolve through the status board + lifecycle guards. Each
read site moved to `is_enabled(...)`; defaults were set to match verified live
behaviour exactly (prod compose arms `DECISION_ANALYSIS_QUEUE_ENABLED=1`, so
dev=off/prod=on; `current_env()` mirrors `config.is_development`, so `CSRF` and
the test suite resolve as before). `CASH_LEAVE_NARRATIVE_DISABLED` was de-inverted
to `CASH_LEAVE_NARRATIVE_ENABLED`; `SARCASM_DETECTION_ENABLED` (a hardcoded `True`
constant) became a real STABLE flag. The stages shipped as suggested below:

| Flag | Suggested stage | Note |
|---|---|---|
| `MODERATION_ENABLED` | STABLE | On by default; clear kill switch. |
| `WORLD_TICKER_ENABLED` | STABLE | On by default; core cash-mode loop. |
| `TICKER_ASYNC_NARRATION_ENABLED` | STABLE | On by default. |
| `ENABLE_AVATAR_GENERATION` | STABLE | On by default. |
| `ENABLE_AI_COMMENTARY` | STABLE | On by default. |
| `DECISION_ANALYSIS_ENABLED` | STABLE | On by default. |
| `DECISION_ANALYSIS_QUEUE_ENABLED` | STABLE, dev=False/prod=True | Mirrors the Director-thermostat dev/prod split idiom. |
| `ENABLE_AI_DEBUG` | EXPERIMENTAL | Dev/debug opt-in. |
| `ENABLE_TEST_ROUTES` | EXPERIMENTAL | Dev/test opt-in. |
| `CASH_LEAVE_NARRATIVE_DISABLED` | — | Migrate as a **non-inverted** `CASH_LEAVE_NARRATIVE_ENABLED` (STABLE, on). Inverted `_DISABLED` flags read backwards through the board. |
| `SARCASM_DETECTION_ENABLED` | STABLE or GRADUATE | Today a hardcoded `True` module constant with no env hook — a half-built flag. Either migrate to a real registry flag (STABLE) or, if sarcasm detection is permanent, drop the constant and graduate. **Decide, don't leave it as a dead `True`.** |

Security-sensitive — migrate carefully, last:

| Flag | Suggested stage | Note |
|---|---|---|
| `CSRF_PROTECTION_ENABLED` | STABLE, dev=False/prod=True | Already has the right dev/prod split; the registry can express it natively. Migrate with a test that prod resolves ON. |

### Hold as-is (out of registry by design)

- **Debug/trace**: `MOVEMENT_TRACE`, `MOVEMENT_TRACE_ALL`, `MOVEMENT_TRACE_FILE`
  — diagnostic-only, no product behaviour. Leave as raw env reads.
- **Non-boolean modes/tunables**: `VICE_MODE`, `LEVER_REFERENCE_MODE`,
  `SOCKETIO_ASYNC_MODE`, `GENESIS_RESERVE_RATIO`, `LLM_*_BUDGET_USD`,
  `LLM_PROMPT_RETENTION_DAYS` — not booleans; the registry is boolean-only.
- **LLM model tiers + `LLM_PROMPT_CAPTURE`**: already DB-backed via the admin
  Settings UI. Established, separate mechanism. Leave as-is.
- **Frontend Vite flags**: build-time, not runtime-resolvable. Separate system.

---

## Suggested order of execution

1. ✅ **`PRESENCE_SHADOW_WRITE_ENABLED` removed** (2026-06-10) — see the Kill table above.
2. ✅ **Retired `REGEN_ENABLED`** (2026-06-10) → RETIRED + removed from the conftest reset; bankroll branch left as dead-pending-cleanup.
3. **Resolve the `RAKE_ENABLED` GRADUATED-vs-live-knob inconsistency** (do NOT delete it — it's a live sink toggle).
4. ✅ **Closed the Director-thermostat drift** (2026-06-10): promoted dev=True on all five; dev now runs the prod economy.
5. ✅ **Migrated the non-registry booleans** (2026-06-10) — moderation, ticker ×2, commentary, avatar, decision-analysis ×2, debug, test-routes, sarcasm, de-inverted leave-narrative. See the migration section above.
6. ✅ **Migrated `CSRF_PROTECTION_ENABLED`** (2026-06-10) — STABLE dev=off/prod=on; `test_csrf.py` + the full app/config suite pass.
7. **Tilt flags: revisit after the emotional-reachability fix** — then promote `TILT_CONDITIONING_ENABLED` or retire it.
8. **Graduate `CHIP_CUSTODY_*`** only once you're certain the pre-ledger path is never coming back.
9. **`PRESENCE_AUTHORITY_ENABLED`** branch cleanup (large; whenever convenient).
10. **Time-boxed experimentals** (`DIRECTOR_INEQUALITY_RAKE`, `CASINO_RELATIVE_THRESHOLDS`, `TABLE_AFFINITY_ENABLED`): promote or kill at the next economy pass.
