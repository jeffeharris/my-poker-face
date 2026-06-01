---
purpose: Scope for a per-player skill spectrum — make some tiered bots genuinely sharp and others believably weak, by scaling the existing adaptive stack
type: design
created: 2026-06-01
last_updated: 2026-06-01
---

# Player skill spectrum — scope

## Status

- **Phase 1 (skill model):** done — tier set + decisions finalized (below).
- **Phase 2 (config + apply seam):** **DONE.** `poker/strategy/skill_tiers.py`
  (`SKILL_TIERS` + `apply_skill_tier`), wired into `flask_app/handlers/tiered_factory.py`
  (`build_tiered_controller(skill=…)` + the `'sharp'` branch of `build_controller`),
  and an eval hook `SKILL_TIER=` in `experiments/measure_passivity.py`. Default tier
  is the no-op ceiling, so production behavior is unchanged. Tests:
  `tests/test_strategy/test_skill_tiers.py` (11, green).
- **Phase 3 (validate monotonicity):** **PASS.** Three independent instruments on
  `measure_passivity` (hero = Baseline, heads-up, 2000h × seeds 42/142/242) all show a
  clean monotone skill drop shark→weak_reg→rec:
  - **Readability (tell map, vs station `jeff`):** shark's river big bet is balanced
    (34% bluff share vs 37% GTO, gap −3%; 208 river bluffs fired). weak_reg leaks a
    **face-up 1.0× size** (95% value) while its overbet stays ~balanced. rec drops
    overbets entirely (`overbet_fraction=0` → no xl size), fires **0 river bluffs**,
    and its river is **face-up** (98% value). The instruments *see* the skill drop.
  - **Value extraction (bb/100 vs station):** +59.7 > +49.5 > +32.6 — monotone; the
    shark pulls ~2× from the same donor (all three still beat the leaky station, as
    expected — balance under-exploits a fish, BUILD_A_BETTER_BOT.md).
  - **Stab-defense (vs aggressive reg `punisher`):** fold% facing a bet 24% < 32% <
    39%; fold% with *air* (the capped check range the stab attacks) 41% < 58% < 71%.
    rec over-folds its air to the stabber where shark defends — the sensitive direct
    metric is cleanly monotone. (bb/100 vs the stabber: +25.7 / +19.2 / +21.0 — shark
    tops; the weak_reg↔rec order is within bb/100 noise, the metric this harness
    explicitly warns is too insensitive for postflop deltas — the fold% ladder is the
    authoritative read.)
  - **`exploitation_strength` axis** is NOT exercised here (the Baseline hero has
    `anchors=None`, so the exploitation layer no-ops). Its endpoints (1.0 vs 0.0) are
    already validated by `exploit_bb100.py`; the tiers interpolate monotonically
    (1.0 ≥ 0.7 ≥ 0.4 ≥ 0.1) between them. An anchored-hero re-run is optional.
- **Phase 4 (author roster):** **DONE.** `skill` is now read per-persona in
  `TieredBotController.__init__` (mirroring the `adaptive_overbet` read — native to
  every live build path; the factory `skill=` kwarg still wins as an explicit
  override, an unknown tier logs + falls back to the ceiling). The roster is authored
  in `poker/personalities.json` keyed to each character's existing `adaptation_bias`
  band (the signal already encoding "how sharp is this character"), composing with —
  not overriding — their aggression/looseness charts:
  - `adaptation_bias ≥ 0.65 → shark` (6): Socrates, Sherlock Holmes, Sun Tzu,
    Machiavelli, Cleopatra, Queen Elizabeth I — the deductive/strategic readers.
  - `0.45–0.64 → reg` (14): Churchill, Napoleon, Louis XIV, Franklin, Twain, Houdini,
    Wilde, Marie Antoinette, Agatha Christie, Lady Macbeth, Robin Hood, Cheshire Cat,
    Medusa, The Fortune Teller.
  - `0.25–0.44 → weak_reg` (23): the broad mediocre middle — incl. the high-aggression
    maniacs (Blackbeard, Zeus, Queen of Hearts, Honey Badger) who become *aggressive
    but exploitable* (active chart + weak skill ≠ passive — and ≠ spewy chaos bot).
  - `≤ 0.2 → rec` (7): Buddha, Bob Ross, Dr. Seuss, Jesus Christ, The Grandmother,
    The Kindergarten Teacher, the dad-jokes guy — gentle/passive face-up over-folders.
  - **Excluded (12):** the 9 `archetype=fish` tourist personas (the rule-bot floor
    *below* the spectrum) + the 3 bot reference personas (CaseBot/GTO-Lite/BaselineSolver).
  - **Effect is staged, not live:** the field only changes after the DB is re-seeded
    from the JSON (`seed_personalities_from_json` → `config_json`, which round-trips
    arbitrary keys). Until then, production is unchanged. Untagged personas default to
    the no-op ceiling.
  - Tests: `tests/test_strategy/test_skill_tier_persona_read.py` (5, integration; live
    persona→tier wiring incl. the unknown-tier fallback).

### Phase-1 decisions (finalized)

1. **Adaptive axis = `exploitation_strength` only.** `adaptation_bias` lives on
   the frozen `PersonalityAnchors` and already multiplies into the exploitation
   product alongside `exploitation_strength`; the tier sets only
   `exploitation_strength` to avoid double-counting that product and to leave each
   persona's authored personality intact. The `adaptation_bias` column below is
   therefore *descriptive* of the typical persona, not enforced by the tier. (If
   Phase 3 shows we need finer top-end control, a controller-level
   `adaptation_bias` override is a one-line read-site add at
   `tiered_bot_controller.py:1738`.)
2. **No `personalities.json` read in Phase 2** — the mechanism ships now; per-persona
   tier assignment is pure data authoring in Phase 4.
3. **Default tier is a no-op** — it never writes fields, so post-construction
   customization (e.g. the fish path's `overbet_fraction` / `_deviation_profile`)
   is never clobbered.

### Reconciliation with the code (important)

The draft table below assumed today's bot was a `reg` at `exploitation_strength=0.7`
with a sharper `shark` above it. **The constructor defaults are actually
`(exploitation 1.0, river_bluff 1.0, stab_defense 0.5, overbet 1.0)` — today's
production bot is ALREADY at the validated ceiling.** So `shark` *is* today's
default (no headroom above it — "no sharper than validated"), and
`reg`/`weak_reg`/`rec` are progressively **weaker new tiers below it**. The
default/no-op tier is therefore **`shark`**, and the plan's vision of "a long tail
of regs weaker than the sharks" is realized in Phase 4 as a deliberate, validated
roster shift — not silently in Phase 2.

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

As implemented in `SKILL_TIERS` (the tier sets the **bold** columns; `adaptation_bias`
is descriptive only — see Decision 1):

| tier | **exploitation_strength** | adaptation_bias | **river_bluff_fraction** | **stab_defense_intensity** | **overbet_fraction** | feel |
|---|---|---|---|---|---|---|
| `shark` (default / ceiling = today's bot) | 1.0 | ~0.7 | 1.0 | 0.5 | 1.0 | reads, balanced, defends |
| `reg` | 0.7 | ~0.5 | 1.0 | 0.5 | 1.0 | solid: softer reads |
| `weak_reg` | 0.4 | ~0.3 | 0.5 | 0.25 | 0.5 | half-baked: semi-face-up, soft adapt |
| `rec` | 0.1 | ~0.15 | 0.0 | 0.0 | 0.0 | face-up, over-folds, doesn't adapt |
| (`fish`) | — rule bot — | | | | | existing floor, separate controller |

(Values illustrative — Phase 3 validates/tunes them. `shark` is pinned to the
constructor defaults; weakening the others is free, so they need no eval.) A continuous `skill ∈ [0,1]`
that interpolates the bundle is a trivial later add if finer control is wanted.

## Build seam

- **Config (Phase 4):** add `skill` (tier name) to the per-personality config —
  same seam `adaptation_bias`/anchors already use (`personalities.json` / archetype
  config). **Default = `shark`** (today's ceiling values) so nothing changes until a
  weaker tier is assigned. (Deferred to Phase 4 per Decision 2 — Phase 2 ships the
  mechanism + a `skill` kwarg defaulting to the no-op tier.)
- **Apply (DONE):** `apply_skill_tier(controller, tier)` in
  `poker/strategy/skill_tiers.py` sets the intensity fields from the tier table.
  Called at build time after construction — production via
  `flask_app/handlers/tiered_factory.py` `build_tiered_controller(skill=…)` (the
  same post-construction seam the fish path uses); eval via the `SKILL_TIER=` env
  hook in `measure_passivity.py`. The default tier is a no-op so it never clobbers
  post-construction customization.
- **adaptation_bias overlap — RESOLVED (Decision 1):** the tier does **not** set
  `adaptation_bias`. It lives on the frozen `PersonalityAnchors` and already
  multiplies into the exploitation product alongside `exploitation_strength`, so the
  tier drives the adaptive axis through `exploitation_strength` alone — no
  double-count, no anchors mutation, persona personality preserved. (`exploitation_strength=0.1`
  already drags the whole product near zero for `rec` regardless of the persona's
  authored bias.)

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

1. **Skill model — DONE** — tier set + knob→tier table + adaptation_bias decision
   (Decision 1) finalized. (Design only.)
2. **Config + apply seam — DONE** — `apply_skill_tier` helper + `skill` kwarg
   (default = no-op `shark`) wired at the production build site + eval hook. Per-persona
   `skill` field in `personalities.json` deferred to Phase 4 (Decision 2).
3. **Validate monotonicity** — the ladder checks above with the existing instruments;
   the `SKILL_TIER=` env hook on `measure_passivity` drives a tier per run;
   tune the tier values so the ladder is cleanly monotone and the weak tiers visibly
   leak to the reader/stabber.
4. **Author the roster — DONE** — `skill` read per-persona in
   `TieredBotController.__init__`; tiers assigned across the 50 non-fish/non-bot
   celebrities keyed to their `adaptation_bias` band (6 shark / 14 reg / 23 weak_reg /
   7 rec). Staged in `personalities.json` — takes effect on DB re-seed.
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
