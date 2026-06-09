---
purpose: Diagnostic analysis of the AI emotional/psychology system — where personas can reach on the chart, why tilt and the chart edges are nearly unreachable, the zone→decision coupling map, and the decisions a rebalancing pass must make
type: design
created: 2026-06-09
last_updated: 2026-06-09
---

# Emotional System Analysis (reachability, tilt scarcity, coupling)

## Why this doc

The believability push (`docs/plans/PERCEPTIBILITY_CONDITIONING.md`,
`docs/technical/ARCHETYPE_SHAPING_FINDINGS.md` §C) wants tilt to be a
*perceptible, exploitable state* — "a high frequency is realistic; a constant
high frequency is a caricature." But on inspection the AI personas almost never
reach tilt, and more broadly almost never reach **any** edge of the
confidence×composure chart. This doc diagnoses *why*, maps *where each persona
can actually go*, and inventories *how emotional state is wired into decisions*,
so a rebalancing pass starts from facts rather than intuition.

**Headline:** the tilt scarcity is **not a bug — it is an enforced tuning
target** (PRD: "Full Tilt 0–2%"), enforced by a hard baseline clamp plus
stability-tuned recovery. It is enforced strongly enough that it now **starves
the `tilt_conditioning` feature** (which gates on `composure < 0.40`): the spike
can't fire because composure rarely crosses the line. **Reachability is upstream
of every other tilt ambition** (new tilt types, the telegraph). Fix reachability
first, or the rest fires on nothing.

> Confidence note: the per-event arithmetic below is *derived from the code's
> constants*, not yet from a live run. The first action after this doc is to
> **measure the actual time-in-zone distribution** (re-run
> `experiments/psychology_balance_simulator.py` and/or pull live
> `player_decision_analysis` zone columns) to confirm with numbers. The
> structural findings (the clamp, same-hand recovery, the coupling map) are
> verified against the code.

---

## 1. Prior analysis — it survives and is re-runnable

We calibrated this system before; the tooling is intact:

- **`experiments/psychology_balance_simulator.py`** (~1025 lines) — Monte-Carlo
  of composure/confidence dynamics, explicitly targeting **2–7% tilted time**;
  bins into composure bands and runs parameter sweeps.
- **`experiments/configs/zone_*.json`** — ~13 saved tuning variants (recovery,
  thresholds, radii, gravity/no-gravity, pressure-balanced) + **`zone_parameters.json`**
  (the *live* params) + `tiered_psychology_drift.json`, `groq_8b_emotional_range.json`.
- **`experiments/analysis/`** (`ZoneMetricsAnalyzer`, `ZoneReportGenerator`) and
  **`experiments/tuning/`** (`ZoneParameterTuner`).
- **PRD target distribution** (`docs/technical/PSYCHOLOGY_OVERVIEW.md`):
  Baseline 70–85% / Medium 10–20% / High 2–7% / **Full Tilt 0–2%**.

The 4D emotion model (valence/arousal/control/focus) was **retired** (schema
v136, commit `83ec820a`); the current model is the **EmotionFamily × Quadrant
matrix**. Zone "gravity/stickiness" was scaffolded but **never implemented**
(removed 2026-05-15, see `docs/triage/ZONE_GRAVITY_DECISION.md`).

Reference docs: `PSYCHOLOGY_OVERVIEW.md` (architecture), `PSYCHOLOGY_DESIGN.md`
(philosophy), `PSYCHOLOGY_ZONES_MODEL.md` (1488-line spec),
`EMOTION_AND_PRESSURE_ARCHITECTURE.md` (the three equity-fed tracks).

---

## 2. The chart and where personas can reach

The map is a **confidence × composure** plane (3rd axis: **energy**, driving
manifestation), partitioned into:

- **Quadrants** (`psychology_model.py:521`): COMMANDING (conf>.5, comp>.5),
  OVERHEATED (conf>.5, comp≤.5), GUARDED (conf≤.5, comp>.5), SHAKEN (both ≤.5).
- **Sweet-spot zones** (circular, `zone_detection.py:252`): guarded,
  poker_face, commanding, aggro.
- **Penalty zones** (edge-based, `zone_detection.py:279`): `tilted` (comp<0.35),
  `overconfident` (conf>0.90), `timid` (conf<0.10), `shaken`, `overheated`,
  `detached`.

**Two different "tilted" thresholds** (a real source of confusion):
- penalty-zone `tilted` = `comp < 0.35` (`PENALTY_TILTED_THRESHOLD`)
- `emotional_state` descriptor `'tilted'` = `composure < 0.40`
  (`emotional_state.py:63-70`) — **this is the one the `tilt_conditioning`
  feature gates on.**

A persona's **baseline** position is derived from its anchors
(`psychology_model.py:497-518`):

```
risk_mod  = (risk_identity − 0.5) · 0.3
baseline_composure = 0.25 + poise·0.50 + (1−expressiveness)·0.15 + risk_mod
           clamped to [ min(0.55, 0.35+0.05)=0.40 , 0.95 ]
```

The docstring states the intent explicitly: *"clamped to a safe range to stay
outside the TILTED penalty threshold."*

**Reachable region per persona:** most of the roster sits at **poise 0.65–0.82
→ baseline composure ≈ 0.65–0.79**, in a narrow central band. The penalty edges
are effectively out of reach for the median persona. Only ~3 low-poise personas
(maniac archetypes at poise 0.25/0.30, Honey Badger 0.28) baseline low enough
(~0.52) to reach tilt from a single event — and they recover fastest.

---

## 3. Why so little tilt (and so few edges)

Six compounding causes, highest leverage first:

1. **Baseline clamp floors composure at 0.40** (`psychology_model.py:515`,
   `min_comp = min(0.55, tilted_thresh+margin)`). No persona *rests* below the
   `emotional_state` tilt line; recovery always pulls back to ≥0.40. **The single
   biggest lever.**
2. **High median poise** → the composure sensitivity filter
   `sensitivity = floor + (1−floor)·(1−poise)` (`zone_config.py:233-249`) cuts a
   typical persona's event deltas by ~40%.
3. **Recovery fires the *same hand* as the event** — `process_hand` runs
   resolve → composure-update → recover in one pass (`psychology_pipeline.py`),
   so the worst beat is partially healed before the next decision.
4. **Recovery is tuned for stability** (`RECOVERY_BELOW_BASELINE_FLOOR=0.60`,
   `_RANGE=0.40` in `zone_parameters.json`) → a tilted persona climbs back above
   0.40 in ~4–6 uneventful hands.
5. **One-per-category event-stacking cap** (`player_psychology.py:650` /
   `resolve_hand_events`) → the worst *realistic single-hand* composure drop for
   a median persona is ~−0.25 to −0.30, landing them *at* 0.35–0.40, never deep.
6. **Recovery is unconditional + folds are free ticks** — a 6-max AI folds
   ~60–70% preflop; every sat-out hand recovers composure.

### The arithmetic (derived from the constants)

Event deltas live in `_PRESSURE_IMPACTS` (`player_psychology.py:140-208`); the
worst composure event is `bad_beat` at raw −0.35, then sensitivity-scaled.

- **Median persona** (poise 0.65, baseline 0.65): `bad_beat` sensitivity ≈ 0.61
  → actual −0.214 → lands at **0.436** (above the 0.40 line — *doesn't even
  tilt* on a clean bad beat). Needs bad_beat + stacked events the *same hand* to
  cross.
- **High-poise persona** (poise ≥0.78, most of the roster): `bad_beat` → ≈ −0.186
  → lands ~0.51 — "rattled," never close to tilt.
- **Low-poise persona** (poise 0.25, baseline ~0.52): `bad_beat` sensitivity 0.85
  → −0.298 → **0.22**, genuinely tilted — but recovers above 0.40 within ~4 hands.

**Critical linkage:** `tilt_conditioning` requires `emotional_state.state ==
'tilted'` (comp<0.40) **and** a `pressure_source`. Because composure rarely
crosses 0.40, the spike rarely fires — exactly why the earlier probe showed
`tilt_fired=0` at baseline. **Adding new tilt *types* changes nothing until
personas can reach the tilt zone.**

---

## 4. Zone → decision coupling map

Emotional state reaches decisions through *different* mechanisms per bot type,
with no shared coupling point. Two emotional-state objects coexist and are easy
to confuse: **`EmotionalState`** (`emotional_state.py`, LLM narration text only,
post-4D) vs **`EmotionalShift`** (`bounded_options.py`, the decision-layer object
derived from zone penalties via `get_emotional_shift()` — `.state/.severity/.intensity`).

| Coupling | Bot(s) | Effect | Status |
|---|---|---|---|
| `_zone_to_tilt_factor` → scales the **entire exploitation layer** (`tiered_bot_controller.py:4067`) | sharp | shaken/dissociated **zeroes ALL opponent adaptation** (exploitation, value/bluff/induce overrides) | **Load-bearing — but a cliff, not a taper** |
| `compute_trait_offsets` emotional offset (`personality_modifier.py:166-184`) | sharp | `intensity·(1−poise)` logits per action | Load-bearing (secondary to personality term) |
| Bounded-options emotional window shift (`bounded_options.py:1360-1463`) | standard/lean | adds/removes options from the LLM menu | Load-bearing but indirect (LLM still picks) |
| `get_prompt_section` + `apply_zone_effects` (`player_psychology.py:1243,1277`) | chaos/std/lean | prompt text + intrusive thoughts / degradation | Soft (LLM may ignore) |
| `tilt_conditioning` spike (`tilt_conditioning.py`) | sharp / maniac only | aggression in re-raise spots | Was vestigial (flag off); **now ON in dev** |
| `energy` → narration/gesture gate (`controllers.py:933`) | all | speech/gesture frequency | Presentation only |
| `get_display_emotion` → opponents' prompts (`controllers.py:2558`) | all | qualitative label others see | Presentation (indirect) |
| `SizeContext.emotional_state` (`sizing_tendencies.py:158`) | sharp | none — never read by `resolve_size_multiplier` | **Dead wire — remove** |

Two items for the "reconsider the coupling" lens:
- **The exploitation cliff.** A shaken sharp bot instantly loses *all* its reads.
  That's abrupt, and arguably backwards — tilt should make a bot *over-apply*
  reads (force the issue), not forget them. Candidate for a taper and/or a
  direction rethink.
- **Fragmentation.** sharp / standard / chaos couple emotion to decisions in
  three unrelated ways, so "balancing the emotional system" is really balancing
  three systems. A rebalancing pass should decide whether to unify the entry
  point (`get_emotional_shift`) or keep them separate and tune each.

---

## 5. Open decisions (for the rebalancing pass)

1. **Tilt target — deferred until measured.** Is "Full Tilt 0–2%" still right now
   that tilt is meant to be a *felt, exploitable* feature? Likely the volatile
   archetypes should reach tilt meaningfully more often (≈5–10%) while most
   personas stay stable — but **decide after the live distribution is measured**.
2. **Relax the 0.40 baseline clamp** (at least for low-poise archetypes) and/or
   **slow same-hand recovery** so excursions persist long enough to be felt and
   exploited. Quantify against the simulator before/after.
3. **The exploitation cliff** — taper instead of zero, and/or flip the direction
   (tilt = over-apply reads).
4. **Remove the dead `SizeContext.emotional_state` wire.**
5. **Telegraph (downstream of 1–2):** probabilistic chat trigger on *entering*
   tilt + feed the LLM the tilt *state* + loose *suggestions* (not fixed lines —
   varied phrasing so it isn't memorizable), since the avatar already leaks the
   mood (tilt zone ≠ poker-face) and post-hand commentary already fires.
6. **New tilt types** (`hate_losing`, `entitlement`, activating `bluff_called`) —
   require new upstream `pressure_source` triggers + downstream rules; **only
   meaningful after reachability (1–2) is fixed.**

## 6. Recommended sequence

1. **Measure** the current actual time-in-zone distribution vs the PRD target
   (this is the next concrete step after this doc).
2. **Decide** the target distribution (#1 above).
3. **Rebalance reachability** (#2) — clamp/recovery — validate with the simulator.
4. **Then** telegraph (#5) + new tilt types (#6) have something to fire on.
5. **Coupling cleanup** (#3, #4).

---

## Cross-references

- `docs/plans/PERCEPTIBILITY_CONDITIONING.md` — the tilt-conditioning feature this feeds.
- `docs/technical/ARCHETYPE_SHAPING_FINDINGS.md` §C — the believability thesis.
- `docs/technical/PSYCHOLOGY_OVERVIEW.md` / `PSYCHOLOGY_ZONES_MODEL.md` — the system spec.
- `docs/technical/EMOTION_AND_PRESSURE_ARCHITECTURE.md` — the three equity-fed tracks.
- `experiments/psychology_balance_simulator.py` + `experiments/configs/zone_*.json` — the re-runnable calibration tooling.
