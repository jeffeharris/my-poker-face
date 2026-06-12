---
purpose: The authoritative layer model for the tiered bot's strategy — base chart, persona flavor nudge, situational overrides, and psychology as an independent influencing layer — mapped to the decision pipeline
type: architecture
created: 2026-06-12
last_updated: 2026-06-12
---

# Strategy layers

The tiered bot's decision is a stack of composable layers. This is the organizing
model for all strategy/exploitation work. It already exists in the pipeline
(`tiered_bot_controller._get_ai_decision` / `_get_postflop_decision`); the open
work is *what each layer keys on*, not the structure.

## The trunk + tendency model (the unification)

The charts should be **one semi-generalized TRUNK** — a solid, competent baseline
range — that every archetype *bases off of*. An archetype is then **trunk + a
defined layer of tendencies/deviations**. Those deviations do triple duty:

1. **Identity** — they make the character feel distinct.
2. **Exploitable leaks** — the deviations from solid play ARE the leaks
   (`EXPLOIT_CATALOG.md`): a station = trunk + {over-call, under-fold, under-bluff};
   a nit = trunk + {over-fold preflop, fold-to-aggression}.
3. **Learnable** — because each leak is a *consistent, defined* tendency (not
   noise), an opponent model accumulates clean evidence and the read matures.

**The symmetry (key insight):** the tendency vocabulary is shared by BOTH directions.
*Construction:* apply tendency T to the trunk → an archetype that exhibits leak T.
*Exploitation:* detect leak T in an opponent → apply the counter from the catalog.
Same catalog, two sides. (This also explains the clone-fidelity gaps — the authored
leak didn't manifest because it wasn't a clean, consistent tendency layer.)

Today this is *half*-built: `build_archetype_charts.py` derives the 7 width charts
from the base 6-max chart via ad-hoc transforms, baked into static files (which the
audit found drift/inconsistent — `weak_station` inversion, redundant `wider_rfi`).
The refinement: replace the N drifting static charts with **trunk + an explicit,
enumerable tendency layer** (the catalog), applied at build (cached) or runtime.

## The layers (in pipeline order)

### (1) Base chart = the TRUNK (+ archetype tendency layer)
`_select_preflop_table(...)` picks a chart; `lookup_with_fallback(node)` reads the
range. **Today** it picks one of N hero-identity charts (`ARCHETYPE_WIDTH_TABLE`) +
depth (`depth_strategy_tables`). **Target:** one trunk + the archetype's tendency
layer (above). Postflop: `lookup_postflop_with_fallback`.

**Open work — gear-switching (the coarse exploit lever):** also key the range on the
**opponent read** (detection prereq). vs a detected station → a value-wide gear; vs a
nit → a steal-wide gear; vs a maniac → a tight/trap gear. A whole-range change is a
*real* behavioral shift (unlike a logit nudge). With the trunk model this is natural:
a gear is just the trunk + a chosen tendency set. Scope: cleanest HU /
one-dominant-villain; multiway leans on layer (3). See `EXPLOIT_CATALOG.md`.

### (2) Persona flavor nudge — character variance, always-on
`modify_strategy(base_strategy, deviation_profile, emotional_state)` (the
personality-distortion layer; `DEVIATION_PROFILES`, gated by
`skip_personality_distortion`). **This is IDENTITY flavor, not exploitation** — a
small logit nudge that makes each character feel distinct (e.g. "plays pairs a bit
more in every spot"), applied in all situations. Subtle by design.

**Key reframe:** a soft logit nudge is the RIGHT tool here (you *want* it subtle) and
the WRONG tool for exploitation (which must change behavior — proven: a maxed
exploitation nudge moved bluff rate 59.9%→59.8%). So: **nudge = flavor; exploit =
chart-switch (1) + override (3).** Don't try to exploit through this layer.

### (3) Situational reads / overrides — detected-spot adjustments
The override/exploit layers: `_apply_exploitation`, `_apply_value_override`,
`_apply_induce_override`, `_apply_bluff_catch_override`, `_facing_all_in_preflop_veto`,
`_layer_multistreet_context`, `_layer_overbet_context`, `_apply_math_floor`. These
fire when a specific situation/read is detected and are harder to express as a chart.

**Open work:** the exploit-driven ones must produce *real* behavioral change — large
magnitude or a **hard action override** (like `value_override` / the all-in veto
already do), not the current ~−0.1 logit nudge. Each is scope-gated (who/where/when/
depth — see `EXPLOIT_CATALOG.md`) and confidence/sample-gated.

## Psychology — an independent layer that INFLUENCES strategy
The psychology system (emotional state, tilt, pressure) runs on its own but feeds the
strategy stack: `emotional_state = get_emotional_shift(self.psychology)` is threaded
into (2) `modify_strategy` and into the situational layers; `_zone_to_tilt_factor`
scales `exploitation_strength` (composed 1.0 → tilted/overconfident 0.5 →
shaken/dissociated 0.0); `_layer_tilt_conditioning` and the emotional window-shift
apply emotion-driven distortions.

**Principle: you can't be on tilt and out-levelling someone.** Adaptation
(gear-switch + overrides) is gated by emotional state — a composed bot reads and
adapts; a tilted bot reverts to its base/distorted chart and stops exploiting. The
tilt gate already exists for layer (3) via `_zone_to_tilt_factor`; **extend the same
gate to the layer-(1) gear-switch.**

## Putting it together
```
base chart  →  + persona flavor nudge  →  + situational overrides  →  sample action
 (1) hero id          (2) identity              (3) detected reads
 + opp read           (small, always-on)        (real shift / hard override)
       \__________________  gated/​influenced by  __________________/
                         PSYCHOLOGY (emotional state)
        composed: read → pick a gear (1) → fine-tune per spot (3)
        tilted:   collapse both back to base; stop adapting
```

## Build order (smallest → biggest)
1. **Detection re-keying** (prereq, cheap): `hyper_passive` → postflop AF;
   ungate `high_fold_to_cbet` for multiway. (matrix doc)
2. **Layer-(1) gear-switch**: opponent-read → chart selection, gated on composed
   emotional state. Prove with `exploit_behavior_probe.py` (gear actually changes
   play; doesn't fire when tilted) then bb/100. Start with one gear (vs-nit steal).
3. **Layer-(3) hard overrides** for the postflop exploits charts can't express
   (stop-bluffing-vs-station, barrel-vs-folder). Same validation gate.
4. Persona nudge (2) stays as flavor; psychology stays as the gate. New villain
   stats (`fold_to_3bet`, `wtsd`, …) unlock more catalog rows as needed.
