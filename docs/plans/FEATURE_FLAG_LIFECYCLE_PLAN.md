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
| `REGEN_ENABLED` | EXPERIMENTAL | **Kill → RETIRED** | Passive idle-chip faucet, *already* replaced by `SIDE_HUSTLE_ENABLED` (the docstring says so). One live gate remains at `cash_mode/bankroll.py:187`. Flip to `RETIRED` and delete that branch — **unless** you still want it as a live A/B knob against the side hustle, in which case Hold and say so explicitly. Recommend Kill; the side hustle won. |

### Promote (close the Director-thermostat dev/prod drift)

These five are `STABLE, dev=False, prod=True` — live and proven in prod, but
**off in dev**, so local runs don't exercise the prod economy. The registry was
built specifically to make this drift visible and closeable.

| Flag | Action | Rationale |
|---|---|---|
| `GENESIS_RESERVE_ENABLED` | **Promote dev=True** | Proven in prod for the bank-pool seed. |
| `RAKE_RESERVE_GATED` | **Promote dev=True** | Director two-layer rake; stable in prod. |
| `DIRECTOR_POLICY_HOLD` | **Promote dev=True** | Rake-schedule hold; stable in prod. |
| `VICE_RESERVE_GATED` | **Promote dev=True** | Reserve-gated vice; stable in prod. |
| `CASINO_RESEED_ON_SPENT` | **Promote dev=True** | Lean fish lifecycle; stable in prod. |

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
| `DIRECTOR_INEQUALITY_RAKE` | **Hold (time-box)** | An undeployed Director lever. Decide by next economy pass: promote into the thermostat or kill. Don't let it idle indefinitely. |
| `CASINO_RELATIVE_THRESHOLDS` | **Hold (time-box)** | Relative-threshold casino provisioning, never shipped. Same time-box discipline. |
| `TABLE_AFFINITY_ENABLED` | **Hold (time-box)** | Room-stickiness idea, never shipped. Same. |

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
2. **Kill `REGEN_ENABLED`** → RETIRED + remove the `bankroll.py:187` branch (decide A/B-knob question first).
3. **Resolve the `RAKE_ENABLED` GRADUATED-vs-live-knob inconsistency** (do NOT delete it — it's a live sink toggle).
4. **Decide the Director-thermostat drift**: promote dev=True on the five, or annotate as intentional.
5. ✅ **Migrated the non-registry booleans** (2026-06-10) — moderation, ticker ×2, commentary, avatar, decision-analysis ×2, debug, test-routes, sarcasm, de-inverted leave-narrative. See the migration section above.
6. ✅ **Migrated `CSRF_PROTECTION_ENABLED`** (2026-06-10) — STABLE dev=off/prod=on; `test_csrf.py` + the full app/config suite pass.
7. **Tilt flags: revisit after the emotional-reachability fix** — then promote `TILT_CONDITIONING_ENABLED` or retire it.
8. **Graduate `CHIP_CUSTODY_*`** only once you're certain the pre-ledger path is never coming back.
9. **`PRESENCE_AUTHORITY_ENABLED`** branch cleanup (large; whenever convenient).
10. **Time-boxed experimentals** (`DIRECTOR_INEQUALITY_RAKE`, `CASINO_RELATIVE_THRESHOLDS`, `TABLE_AFFINITY_ENABLED`): promote or kill at the next economy pass.
