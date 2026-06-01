---
purpose: Scope for a per-player skill spectrum — make some tiered bots genuinely sharp and others believably weak, by scaling the existing adaptive stack
type: design
created: 2026-06-01
last_updated: 2026-06-01
---

# Player skill spectrum — scope

## Goal

Give each tiered (`sharp`) bot a **skill level** so the field has range: some players
genuinely sharp (read + adapt + hard to read + defend), others believably mediocre
(face-up, over-fold, don't adapt) — without a new system. It rides on the stack we
already built: every relevant effect is a **per-instance intensity scalar**, and the
exploitation layer already takes a per-personality skill dial (`adaptation_bias`).

This sits ABOVE the rule-bot fish (which have no adaptive stack — the existing floor)
and fills the middle ground the field currently lacks: tiered players that are tiered
but *not* full-strength.

## What "skill" maps to (the axes already exist)

Sharpness is a coherent bundle, not one trait. Each axis is an existing knob:

| Axis | knob | sharp | weak |
|---|---|---|---|
| Reads + adapts to opponents | `exploitation_strength`, `adaptation_bias` | high | low/0 |
| Hard to read (own betting balanced) | `river_bluff_fraction` | 1.0 | 0.0 (face-up) |
| Defends vs aggression (capped checks) | `stab_defense_intensity` | 0.5 | 0.0 (over-folds) |
| Value overbet sizing | `overbet_fraction` / `enable_overbet_context` | on | dialed down |

**Out of scope for the skill dial (already per-archetype, compose with it):** preflop
width charts (`archetype_preflop_tables`: loose/station/tight), postflop personality
distortion (`DEVIATION_PROFILES`), psychology anchors. A believable weak rec = a
*loose chart* (already) + *low skill intensities* (new). Skill owns the
adaptive/discipline axis; the chart owns preflop width; they compose.

**Gates stay fixed at their principled values** (`river_bluff_min_ftbb=0.6` is the
1.5× bluff breakeven; `stab_defense_min=0.6` is the validated high-precision gate).
Skill scales *intensity* (how much / whether the response fires), NOT the gate
(*who* it correctly applies to). Lowering a gate isn't "sharper," it's looser. (Read
*maturity* — how fast a player trusts a read — is an optional finer skill axis;
defer.)

## Design — named tiers over a continuous scalar

Author as a small set of **named skill tiers**, each a preset bundle (more believable
+ reasonable to hand-assign than a raw 0–1 float). Proposed:

| tier | exploitation_strength | adaptation_bias | river_bluff_fraction | stab_defense_intensity | feel |
|---|---|---|---|---|---|
| `shark` | 1.0 | 0.7 | 1.0 | 0.5 | reads, balanced, defends |
| `reg` (current default) | 0.7 | 0.5 | 1.0 | 0.5 | solid — today's bot |
| `weak_reg` | 0.4 | 0.3 | 0.5 | 0.25 | half-baked: semi-face-up, soft adapt |
| `rec` | 0.1 | 0.15 | 0.0 | 0.0 | face-up, over-folds, doesn't adapt |
| (`fish`) | — rule bot — | | | | existing floor, separate controller |

(Values illustrative — Phase 4 validates/tunes them.) A continuous `skill ∈ [0,1]`
that interpolates the bundle is a trivial later add if finer control is wanted.

## Build seam

- **Config:** add `skill` (tier name) to the per-personality config — same seam
  `adaptation_bias`/anchors already use (`personalities.json` / archetype config).
  **Default = `reg`** (today's values) so nothing changes until a tier is assigned.
- **Apply:** a `apply_skill_tier(controller, tier)` helper sets the intensity fields
  from the tier table. Call it at build time after construction (production:
  `game_handler`/`cash_bot_assignment`, which already build via the real `__init__`;
  eval: the harness already sets these fields, so it can set `skill` too).
- **adaptation_bias overlap:** it already exists on anchors and scales exploitation.
  Fold it into the tier (the tier sets it) OR keep it as a finer per-personality
  override on top of the tier. Decide in Phase 1 — cleanest is *tier sets the
  baseline, anchors may override*.

## Validation (the part that needs the instruments)

- **Weakening is free.** Turning intensities *down* can only make a bot more
  exploitable/face-up — the intent. No eval needed for the weak tiers.
- **Monotonicity (the one real check):** confirm the ladder is monotone in strength.
  Use the instruments we built:
  - `shark` vs `rec` head-to-head (champion/challenger or `measure_passivity`) →
    shark should win clearly.
  - Each tier vs the adaptive **reader** + **stabber**: a weaker tier should bleed
    MORE (face-up river → reader prints; no stab-defense → stabber prints). The
    tell map should show a weak tier's river going face-up and its check-defense
    collapsing — i.e., the instruments should *see* the skill drop.
- **No "sharper than validated" risk:** the top tier = the current validated settings
  (river 1.0, stab 0.5, exploitation 1.0). All tiers are at or below it → safe.

## Phasing

1. **Skill model** — finalize the tier set + knob→tier table + the adaptation_bias
   overlap decision. (Design only.)
2. **Config + apply seam** — `skill` field in per-personality config (default `reg`)
   + `apply_skill_tier` helper + wire at the production build site + eval.
3. **Validate monotonicity** — the ladder checks above with the existing instruments;
   tune the tier values so the ladder is cleanly monotone and the weak tiers visibly
   leak to the reader/stabber.
4. **Author the roster** — assign tiers to the celebrity personalities coherently
   (a few sharks, a long tail of regs/weak-regs/recs), composing with their existing
   charts/anchors.
5. **(Optional, downstream)** stake-scaled fields — low-stakes tables draw weaker
   skill tiers; a casino/career policy, not core.

## Risks / notes

- **Compose, don't conflict** with the existing per-archetype variation (charts /
  deviation / anchors). Skill is an additional, orthogonal axis.
- **Don't double-count `adaptation_bias`** (it already scales exploitation) — the tier
  should *set* it, not stack on top of an independent value.
- **Weak ≠ random.** A weak tier should be *predictable/face-up/over-folding* (human
  rec), not spewy-random (that's the chaos bot — a different controller).
- **The reads still compute** for weak players (the memory manager records
  fold_to_big_bet/stab_frequency regardless); skill weakens the *consumption*
  (intensity), which is the right lever — a weak player has the info but doesn't use
  it well.
- **Effort:** core (model + config + apply + wiring) ≈ the river-bluff wiring job
  (small-medium). Validation = a handful of eval runs on existing harnesses. Roster
  authoring is ongoing.

## Why it's easy now (the precondition is already met)

Every lever is a per-instance field (`river_bluff_fraction`, `stab_defense_intensity`,
`overbet_fraction`, `exploitation_strength`, `enable_*`), the exploitation layer
already multiplies in a per-personality skill dial (`× adaptation_bias ×
exploitation_strength`), and production builds via the real `__init__` so defaults
apply. This is a config-plumbing + preset-table job on a stack that's already
per-player — not a new system. See `OVERBET_BALANCING.md` §5 for the adaptive stack
and the instruments (tell map, adaptive reader/raiser/stabber) that validate it.
