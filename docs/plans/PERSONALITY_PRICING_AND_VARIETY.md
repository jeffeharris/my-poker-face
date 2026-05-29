---
purpose: Methodology + handoff for pricing the tiered bot's personality deviations (EV cost of non-max-EV play) and using that to add bounded, characterful variety to the AI field
type: guide
created: 2026-05-28
last_updated: 2026-05-29
---

# Personality pricing & variety — process + handoff

> **Why this doc exists.** We want the AI players to feel *distinct* (some loose,
> some sticky, some maniacal) — i.e. to play in ways that are deliberately **not
> max-EV**. The tiered bot already does this (bounded personality deviation), but
> the deviations have never been **priced** (how much bb/100 does each personality
> cost, and where does it bleed?). This doc (a) aligns on the experiment process
> so anyone can run it consistently, (b) pre-registers the first experiment —
> pricing the 6 existing profiles — and (c) hands off the session's state. Written
> as a context-transfer artifact; treat the methodology section as the contract.

## TL;DR

The tiered bot plays the +EV solver chart **distorted by a bounded personality
deviation** (`modify_strategy` → `DeviationProfile`). The deviation's `max_kl`
cap is a *guessed* EV-cost limiter. This program **replaces the guess with a
measurement**: run each personality through the paired-CRN attribution gate vs
the baseline to get its **bb/100 cost + per-node localization**, set an **EV
budget** for "acceptable flavor," and use that framework to add new tendencies
with a known price. The strategic payoff: a *priced* variety system creates a
**heterogeneous, exploitable field**, which is what makes the parked
adaptation/exploitation work (sizing-aware defense/attack) finally valuable — an
emergent **skill gradient** across the AI players.

---

## Current state (handoff — what's true as of 2026-05-29)

**Branch `lookup-tables`** (merged with `origin/development`, pushed). All Python
runs in Docker: `docker compose exec -T backend python ...`.

> **NEW-CONTEXT START HERE.** The live frontier is **item 3 — the spot-tendency
> variety system** (the "Item 3 scope" + "Tendency & skill catalog" sections below).
> Mechanism + first leak (slow-play) + per-personality hook are shipped; the catalog
> is the running to-do. Maniac/personality-pricing (the "Results" table) is done. The
> strength-side gameplay layers (1–3 below) are also done — don't re-open them.

**Shipped this session (production gameplay changes, all eval-validated):**
1. **Wider late-position RFI** (`4f5fb311`, pre-session) — CO/BTN/SB GTO-shaped opens.
2. **Multistreet flop+turn barrel-continuation** (`d1781b30`) — `enable_multistreet_context=True`, `multistreet_h1_streets={FLOP,TURN}` (river leg dropped, measured −EV), H2 off. +3–12 bb/100 vs realistic opponents.
3. **Value overbet** (`170a86ac`) — `enable_overbet_context=True`, `overbet_size=150`, classes `{nuts,strong_made}`, streets `{TURN,RIVER}`. **The big one: +40 HU / +77 6-max cumulative vs former self, no regression** (`2329d0eb`).
4. **Spot-tendency variety system (item 3)** — `poker/strategy/spot_tendencies.py` (`apply_spot_tendencies`, general layer) + slow-play leak (priced **free**) + per-personality override hook (`spot_tendencies` key in personalities.json → `TieredBotController.deviation_profile` merge) + the `--a-disable/--b-disable` pricing-gate flag. Defaults OFF. Commits `bdf150fe`/`3973ab25`/`1f63f658`/`ba98183a`. See the catalog for what's next.
5. **Give-up-turn leak (2026-05-29)** — second `spot_tendencies` handler (`_give_up_turn`), the **dual of the multistreet H1 barrel** (first leak whose exploiter is already built). Priced **free** (intrinsic −1.47, jeff −1.54, punisher +0.14; all CI∋0). Turn-only, disjoint from slow-play by hand class. See "Give-up turn" subsection below.
6. **Fit-or-fold + auto-c-bet leaks (2026-05-29)** — `_fit_or_fold` / `_auto_cbet` on two new bounded reshapes (`_pump_fold`, `_pump_aggression`). Both priced **free/+EV** — and that surfaced a methodology finding: a *correct-spot* leak is recognizable flavor but **not exploitable**, so it doesn't close the loop. See "Fit-or-fold + auto-c-bet" subsection + the open design question.

**Key measured findings (don't re-litigate):**
- The cheap chart frontier (frequency, sizing granularity, dimensional coverage) is **tapped**; the remaining strength lever is the **parked solver program** (HU/multiway, expensive).
- The value overbet is **field-dependent**: +42 vs payers, **−24 vs a perfect sizing-reader** (D1 oracle). It's +EV vs the realistic non-sizing-reading field but not robust.
- Sizing-aware opponent modeling (`docs/plans/SIZING_AWARE_OPPONENT_MODELING.md`) is **scoped but parked**: the field doesn't read/exhibit sizing, so the machinery is inert — *until variety creates exploitable tells* (see "strategic payoff" below).

**The personality-deviation system (the thing we're pricing):**
- `poker/strategy/personality_modifier.py` — `modify_strategy(base, anchors, emotional_state, deviation_profile)` distorts the baseline chart in logit space, **bounded** by `max_kl` / `max_per_action_shift`.
- `poker/strategy/deviation_profiles.py` — `DEVIATION_PROFILES`: **`nit, rock, tag, calling_station, lag, maniac`**. Axes: `aggression_scale`, `looseness_scale`, `risk_scale`, `ego_fold_penalty`, + the KL bounds.
- Sim wiring: `simulate_bb100.make_controller` sets `controller._deviation_profile = DEVIATION_PROFILES[profile_key]` (None for `Baseline`, which sets `skip_personality_distortion=True`). `ARCHETYPES[name]` carries `{kind, profile, anchors}`.
- **Spot-specific extension (item 3):** `poker/strategy/spot_tendencies.py` adds *per-spot* tendencies on top of the global scalars (the layer is node/line-aware; the scalars are not). `DeviationProfile.spot_tendencies` (profile-level) + the personalities.json `spot_tendencies` key (per-character) drive it. See "Item 3 scope" + the catalog.

**The eval gates (the pricing instruments):**
- `experiments/ab_node_attribution.py` — **paired-CRN first-divergence per-node attribution** (the primary pricing tool). Already supports `--a-mode/--b-mode` (multistreet), `--overbet-a/-b`, `--adaptive-opp` (D1 oracle), `--h1-streets`, `--heads-up`, `--stack-bb`. **`--a-hero/--b-hero`** (per-arm hero archetype) — BUILT 2026-05-28; control `--a-hero Baseline --b-hero Baseline` = 100% NO_DIVERGENCE / +0.00, verified. Local self-play roster: `baseline` (= `['Baseline']*5`).
- `experiments/measure_passivity.py` — Tier-A diagnostics + `--leak-report`.
- For pricing a **spot tendency**: `--a-disable/--b-disable layer:rule` (BUILT 2026-05-29) toggles one layer-rule per arm. Recipe: configure a carrier (a profile or a personality's `spot_tendencies`) with the tendency, then A/B `--a-disable spot_tendencies:<name>` (OFF) vs ON on the same `--hero`; the paired delta is the tendency's marginal cost. Control = identical disables → 100% NO_DIVERGENCE.
- `experiments/champion_challenger.py`, `experiments/sng_runner.py`, `experiments/exploit_bb100.py` — other gates (now also ours; parallel session wrapped).

---

## Methodology — how we price a personality (the contract)

**Definition.** A personality's **price** = the bb/100 EV cost of playing the
baseline +EV chart *with* that deviation profile vs *without* it, all else equal.

**Instrument: the paired-CRN attribution gate, hero-archetype A/B.**
- **Arm A** = `Baseline` hero (no deviation, `skip_personality_distortion`).
- **Arm B** = the archetype hero (e.g. `TAG` → `_deviation_profile = DEVIATION_PROFILES['tag']`).
- **Identical** chart, deck, seeds, and opponents across arms (CRN). The personality
  is the *only* difference, so the paired delta (B−A) is its pure cost.
- **`TOTAL bb/100`** = the price (negative = costs EV). **Per-node rollup** = *where*
  the personality first changes play and the EV consequence — the localization that
  distinguishes "characterful (cost spread thin)" from "broken (cost concentrated /
  huge on one node)."

**Why this gate (not self-play win-rate / unpaired runs):** CRN cancels card
variance (the session's load-bearing lesson — `champion_challenger`/SNG nulls were
gate-coarseness artifacts); first-divergence gives the *where*, which is what makes
the price actionable.

**Extension (BUILT 2026-05-28):** `ab_node_attribution` now takes `--a-hero` /
`--b-hero` overrides (default to `--hero`), resolved per arm into `ARCHETYPES[...]`
→ distinct `config_arch` passed to each arm's `_run_one_hand`. Same seat name
(opponents/deck identical), different deviation profile. Mirrors `--a-mode/--b-mode`.
Control: `--a-hero Baseline --b-hero Baseline` = 100% NO_DIVERGENCE / +0.00 (verified).

**Reference — the price is a vector, anchored on SELF-PLAY (corrected 2026-05-28):**
- **PRIMARY anchor = self-play vs `Baseline`** (`--roster baseline`, the bare max-EV
  chart bot — no personality, no overbet/multistreet hero-layers). A one-sided
  deviation vs the reference strategy = the personality's **intrinsic "distance from
  optimal"**, unbiased by any specific opponent's leak, and the *ceiling* of its cost.
  **This is the "is it broken / how far from optimal" number.**
- **SECONDARY = the opponent vector** (`jeff` over-folder, `punisher` reg, optionally a
  station / 6-max) — reported as **"EV vs opponent type"** (the *field-dependence
  profile*), NOT as "the price." The product-relevant cost is vs the realistic *mix*,
  which the vector approximates.
- **Why NOT price on `jeff` alone (the trap we caught):** jeff is a specific
  exploitable over-folder, so "vs jeff" rewards aggression / penalizes tightness —
  that's *jeff's leak*, not the personality's cost. Empirically: Nit prices −5.79 vs
  jeff but ~−50 vs Baseline (the fish masks the tightness cost); maniac reads +9.94 vs
  jeff only because it's *beating up a fish*. A single fish opponent **systematically
  understates** the intrinsic cost. Anchor on self-play; use fish/reg as the vector.
- A personality can be **+EV vs some opponents** (a `maniac` beats a `nit`, loses to a
  competent reg) — that's the vector's job to show.

**Sample/CI convention (session standard):** 8 non-overlapping seed-blocks ×
3000 hands = **24k paired hands**, seeds spaced ≥ hands apart
(`42,3042,6042,9042,12042,15042,18042,21042`); report the 95% CI; treat anything
whose CI spans a budget threshold as unresolved (add hands). HU runs ~3–5 min;
6-max and station-style payers are slower (size jobs to the 10-min ceiling, or
drop per-seed hands and add seeds).

**The EV-budget framework (how to read a price):**
| Price (bb/100, vs the realistic field) | Verdict |
|---|---|
| 0 to ~−5, cost spread across many nodes | **Free/cheap variety** — ship it; this is character |
| ~−5 to ~−15, localized to a few coherent nodes | **Priced flavor** — acceptable if the trait is recognizable/worth it |
| < ~−15, or concentrated on one node, or −EV vs *every* opponent | **Broken, not flavorful** — a bug or an over-tuned `max_kl`; fix or cap |
| **+EV** vs an opponent | the deviation is *exploiting* that opponent (not pure flavor) — note it |
(Thresholds are a starting proposal — calibrate against the priced 6 profiles.)

**What to record per experiment (so results are comparable + transferable):**
1. The exact command. 2. `TOTAL bb/100 + CI` per opponent. 3. Top per-node
contributions (the localization). 4. The budget verdict. 5. Date + commit. Append
to the Results table below; narrate surprises in the captain's log.

---

## Experiment 1 (pre-registered): price the 6 existing profiles

**Hypothesis / question:** what does each of `{nit, rock, tag, calling_station,
lag, maniac}` cost vs `Baseline`, and where does it bleed? Which are real variety
(cheap), which are accidentally broken (huge/concentrated cost)?

**Setup.** Anchor on **self-play (`--roster baseline`)** first; then run the
`jeff`/`punisher` vector. Repeat for ARCH in `{Nit, Rock, TAG, 'Calling Station',
LAG, Maniac}` (exact ARCHETYPES keys — note the space in 'Calling Station'; verify
via `python -c "from experiments.simulate_bb100 import ARCHETYPES; print(sorted(ARCHETYPES))"`).
```
SEEDS=42,3042,6042,9042,12042,15042,18042,21042   # 24k; self-play is high-variance, use the full count
# PRIMARY — intrinsic cost (self-play vs the bare max-EV chart bot):
docker compose exec -T backend python -m experiments.ab_node_attribution \
    baseline 3000 $SEEDS --a base --b base --a-hero Baseline --b-hero <ARCH> --heads-up --top 12
# SECONDARY — field-dependence vector:
... same, with `jeff` and `punisher` in place of `baseline`
```
Note: the self-play reference opponent is the *bare* chart bot — the gate wires the
overbet/multistreet layers onto the hero only, so the Baseline opponent plays the
plain solver chart. That's the right neutral reference (the shipped layers are
themselves exploit-leaning deviations).

**Pre-committed validation / what we learn:**
- Each profile gets a `{vs jeff, vs punisher}` price vector + per-node localization.
- Sanity: directions should match the archetype (e.g. `nit` folds more → loses
  pots it could win vs a folder, may be ~neutral vs a reg; `maniac` spews → large
  −EV vs a station, maybe +EV vs a nit). A direction that contradicts the archetype
  is a wiring bug.
- Flag any profile that is **broken** (< −15 or one-node-concentrated) for a
  `max_kl` re-cap or a deviation-logic fix.

### Results (vector COMPLETE 2026-05-28 — self-play + jeff + punisher; Maniac re-capped)

N per cell: jeff = 12k HU; punisher = 24k HU; self-play 24k for the wide-CI profiles
(LAG, Maniac), 12k for nit/rock/tag/calling_station (CIs already decisive there).

| Profile | **vs Baseline (self-play, INTRINSIC — primary)** | vs jeff (over-folder slice) | vs punisher (reg) | Verdict (budget) |
|---|---|---|---|---|
| nit | **+6.45** [−4.2, +17.1] _(12k)_ | −5.79 [−9.8, −1.8] | −2.39 [−5.2, +0.4] | **free** — CI∋0 vs both competent refs |
| rock | **+4.25** [−6.1, +14.6] _(12k)_ | −6.21 [−10.2, −2.3] | −2.25 [−5.0, +0.5] | **free** — CI∋0 |
| tag | **+0.26** [−10.5, +11.0] _(12k)_ | +3.73 [−1.8, +9.2] | +0.82 [−1.7, +3.3] | **free** — near-GTO, ~0 |
| lag | **−0.89** [−12.1, +10.4] _(24k)_ | +7.20 [+0.25, +14.2] | +0.47 [−3.7, +4.6] | **free** — 24k pulled it −7→~0; CI∋0 everywhere |
| calling_station | **−10.26** [−19.7, −0.8] _(12k)_ | −4.95 [−8.7, −1.2] | −1.38 [−3.9, +1.2] | **priced** — intrinsic CI-clear; ~free vs the reg |
| maniac (pre-recap) | **−15.67** [−30.6, −0.8] _(24k)_ | +9.94 [+0.7, +19.2] | −0.76 [−6.1, +4.6] | **borderline-broken** — CI-clear, FLOP −12.3 → re-capped ↓ |
| **maniac (re-capped 0.60→0.35)** | **−11.30** [−25.5, +2.9] _(24k)_ | +13.08 [+6.9, +19.3] | −0.65 [−5.7, +4.4] | **priced flavor** — off the broken line; fish-exploit + reg-neutrality intact |

**Read 1 — the self-play anchor inverted the jeff ranking (the whole reason to anchor on it):**
- **Intrinsic ranking (cheap→expensive):** Nit/Rock/TAG/LAG ≈ **free** (CI∋0) →
  Calling Station −10 → **Maniac −16** (the costliest, CI-clear).
- **vs jeff was nearly the *opposite*:** Maniac read **best** (+9.94) but is the **worst**
  intrinsically; Nit read **costly** (−5.79) but is **free** intrinsically (+6.45).
  jeff's number was *fish-exploitation*, not personality cost — pricing on jeff alone
  would have inverted the verdict. (Also: a 400h Nit self-play smoke read −50; the 12k
  run is +6.45 — pure noise. Never read a 400h number.)

**Read 2 — the punisher (reg) vector: every profile is CI∋0 (~free vs a disciplined reg, HU).**
Neither the fish (jeff) nor the reg (punisher) *extracts* the intrinsic cost — the personalities
don't bleed much against a competent HU opponent. **The intrinsic self-play number is the real
"distance from optimal"; the field slices are color, not the price.** (The product-relevant cost
is vs the realistic mix, which sits between intrinsic and the fish-flattering jeff slice.)

**Read 3 — 24k pulled the wide-CI profiles toward 0:** LAG −7.05→**−0.89**, Maniac −24.14→**−15.67**.
The 12k point estimates overstated both costs; the tighter runs are the trustworthy ones (same
lesson as the 400h smoke, one order up).

**Read 4 — the Maniac re-cap, and the surprise about which lever bites:**
- Maniac intrinsic −15.67 was CI-clear and **FLOP-concentrated (−12.3 of −15.7 = 79%)** → trips
  both broken-criteria (point estimate at the −15 line AND street-concentrated) → re-cap warranted.
- **`max_kl` is INERT for Maniac.** In `clamp_divergence` the per-action clip runs *before* the KL
  check, and it already pulls realized KL (≈0.95) under the cap, so `max_kl` never engages. Dropping
  `max_kl` 1.2→1.0 was **byte-identical** (same per-node counts). The guessed KL limiter is the wrong
  knob. **The binding lever is `max_per_action_shift`.**
- Swept it (intrinsic self-play 24k): `0.60→−15.7, 0.45→−13.6, 0.35→−11.3, 0.25→−7.5, 0.15→−7.0`
  (knee ~0.25–0.35, then flattens — the residual ~−7 is the scales, not the cap). **Chose 0.35 →
  −11.30:** off the broken line into priced flavor, still the costliest/most flop-aggressive
  profile (FLOP −8.1) — recognizably a maniac, now bounded.
- **Latent clamp bug fixed in passing:** the tighter (now-binding) 0.35 cap exposed that
  `_clip_and_normalize`'s default 10 iterations under-converged (~2e-6 cap residual; broke the
  cap invariant + a unit test). It's slow *linear* convergence (50 iters → 7e-11), not a cycle →
  bumped the default to 100. Price unchanged; `test_strategy` green (1322).

---

## Strategic payoff (why this matters beyond flavor)

Everything we parked — sizing-aware defense/attack, the exploitation layer — died
because the field is **homogeneous** (clones pay, nobody's exploitable → adaptation
inert). A **priced variety system manufactures the exploitable behaviors** those
layers target:
- a face-up-sizing personality is exactly what the D1 oracle / the parked
  sizing-aware C exploits (−24);
- an over-bluffer is what bluff-catch calibration beats;
- a position-blind fish is what stealing punishes.

So variety (the weak/characterful end + exploitable tells) and EV-maximization
(the strong end + the pricing meter) are **complementary**: together they make an
emergent **skill gradient** across the AI players — the texture a poker game with
AI personalities wants, and the thing that revives the parked adaptation work.

## Roadmap after Experiment 1

1. **Price the 6 profiles** (Exp 1) → audit: real variety vs broken vs accidental +EV. ✅ DONE.
2. **Re-cap the binding bound per profile from the measured budget** (replace the guess). ✅
   Maniac done. **Finding: for the aggressive profiles `max_kl` is *inert*** (the per-action
   clip in `clamp_divergence` runs first and pulls realized KL under the cap), so the lever
   that actually bites is **`max_per_action_shift`**, not `max_kl`. Re-cap whichever binds.
3. **New spot/line-specific tendencies** (today's deviations are *global scalars*):
   sizing tells / face-up, slow-play/trap, donk-bet, open-limp, position-blindness,
   spot-specific over/under-bluffing. Each priced + budgeted before shipping.
   **→ SCOPED 2026-05-28, see "Item 3 scope" below.**
4. **Close the loop:** with exploitable personalities in the field, re-judge the
   parked sizing-aware C (attack) + bluff-catch calibration — they now have targets.

## Item 3 scope — spot/line-specific tendencies (2026-05-28)

**Decisions (locked with the user):** build the **general mechanism first**; validate
it on **slow-play** (an easy action-mass reshape) before the harder sizing tell.

### Why the deviation layer can't do this today (the structural fact)
`modify_strategy` is **spot-blind** — it takes only `base / legal_actions / anchors /
emotional_state / deviation_profile` and applies global logit scalars uniformly. The
`node` (position IP/OOP, street, `made_tier`, `draw_modifier`, key-encoded board texture
+ `pot_type` + SPR) and the initiative signals (`was_prev_street_aggressor`,
`preflop_aggressor`, `_find_preflop_raiser_idx`, SPR buckets from `postflop_classifier`)
all **exist at the call site** (`tiered_bot_controller` ~650 preflop / ~878 postflop) —
they're just not passed into the deviation layer.

### Mechanism (general, build once) — mirrors `apply_river_bluff_guardrail`
- **`apply_spot_tendencies(strategy, node, signals, profile)`** — an additive
  post-personality layer that runs right after `modify_strategy` in both decision paths
  (the river guardrail is the existing precedent; it's postflop-only, this is pre+postflop).
- **Each tendency** = a named reshape `(node, signals) → adjusted probs | no-op`, gated by
  per-profile config, **bounded by `clamp_divergence`** (reuse the per-action + KL caps so
  every tendency is EV-bounded like the global scalars), emits an `InterventionTrace`, and is
  **ablatable via `disable_rules`** under a stable rule id (e.g. `spot.slowplay`).
- **Per-profile config:** a `spot_tendencies: {name: strength∈[0,1]}` map on the profile
  (strength scales the reshape; absent/0 = off). **Default profiles ship with NO spot
  tendencies on** until each is individually priced + budgeted in.
- **Signals plumbing:** assemble a small `SpotSignals` from the same fields the multistreet
  layer already reads (initiative, preflop-aggressor==hero, spr_bucket, is-first-in) so sim
  and live agree; the harness already drives the `_sim_*` shadow fields.

### Attaching tendencies — profile-level vs per-personality (BUILT 2026-05-29)
Two attach points, both shipping:
- **Profile-level** — `DeviationProfile.spot_tendencies = (('slowplay', 0.8), ...)`. Affects
  **every** personality that classifies into that archetype (`select_deviation_profile`
  maps anchors → one of the 6 shared profiles). Use for archetype-wide flavor.
- **Per-personality override** — a specific character carries its own tendencies independent
  of its archetype, via a `spot_tendencies` key in its **personalities.json** entry:
  ```json
  "spot_tendencies": [["slowplay", 0.8]]
  ```
  Resolution lives in `TieredBotController.deviation_profile`: it lazy-resolves the archetype
  profile, then merges the override (`dataclasses.replace`). Precedence: an explicit
  `controller._spot_tendencies_override` (sims/tests) > the personality-config key > the
  archetype profile's own `spot_tendencies`. A **non-empty** character config *replaces* the
  archetype's; **absent/empty** inherits; an explicit `()` opts a character *out*. Strength is
  still bounded by the profile's `max_per_action_shift`. Parser: `parse_spot_tendencies`.
  Tests: `test_spot_tendencies.py` (parse + the 5 resolution/precedence cases). Defaults: no
  personality ships a tendency yet — that's a per-character content call (slow-play is priced
  free, so it's safe to attach when desired).

### First tendency — slow-play / trap (mechanism validation)
- **Spot predicate:** `made_tier ∈ {nuts, strong_made}` AND hero has initiative
  (`was_prev_street_aggressor`) AND `street ∈ {flop, turn}` (river slow-play is a different
  animal and the guardrail already touches river).
- **Effect:** shift a `strength`-scaled fraction of aggressive mass (`bet_*/raise_*`) → check,
  bounded by `clamp_divergence`. Trap instead of fast-play.
- **Why first:** cleanest reshape (no sizing dimension), recognizable character, signals exist,
  and its cost (forgone value/protection) should **localize on the strong-hand flop/turn nodes**
  — a sharp test that the per-node attribution prices a spot tendency where we expect.

### Pricing path per tendency (the session contract)
1. **Gate extension needed:** `--a-disable / --b-disable <rule_ids>` on `ab_node_attribution`,
   feeding each arm's hero `disable_rules` (mirror of `--a-hero/--b-hero`). A/B = tendency
   OFF (arm A disables it) vs ON (arm B). Control: identical disables both arms = 100%
   NO_DIVERGENCE / +0.00.
2. **Self-play intrinsic = the price**; jeff/punisher = field vector; **24k**; per-node
   localization; budget verdict (free 0..−5 spread / priced −5..−15 localized / broken <−15
   or one-node). Same bands the 6 profiles calibrated.
3. **Sizing-tell only:** additionally measure **exploitability** via the D1 oracle
   (`--adaptive-opp`, already built) — the point of that tendency is a punishable tell, which
   is what makes roadmap item 4 (the parked sizing-aware attack) finally worth reviving.

### Slow-play — BUILT + PRICED (2026-05-28)

Mechanism shipped (`spot_tendencies.py` + controller wiring + `--a-disable/--b-disable`
gate flag; control = 100% NO_DIVERGENCE, verified; `test_strategy` green 1329). Priced
on a TAG carrier (near-GTO, per-action cap 0.30), strength 0.8 (cap-saturated), A/B =
slow-play OFF (`--a-disable spot_tendencies:slowplay`) vs ON, 24k HU:

| Opponent | slow-play price (bb/100) | verdict |
|---|---|---|
| Baseline (self-play, **intrinsic**) | **−0.16** [−4.78, +4.46] | **free** — CI∋0 |
| jeff (over-folder) | **−5.13** [−7.22, −3.03] | mild cost — the trap backfires |
| punisher (reg) | **−1.56** [−3.16, +0.05] | ~free |

- Fires on ~1% of hands (strong made + initiative + `unopened` + flop/turn); **every**
  diverging node is a `flop/turn|…strong_/nuts` spot, cost spread thin across many (no
  single-node concentration) → the cheap-variety signature.
- **Verdict: FREE/CHEAP variety** — recognizable trappy character at ~0 intrinsic and
  ≤−5 bb/100 worst-case (vs the over-folder field). Shippable; sets the slow-play budget.
- **Finding (validates the variety thesis):** slow-play pays off vs *no* opponent in the
  current field — a trap with no one to trap (the over-folder just takes a free card; the
  reg doesn't over-bluff into a check). Its EV would *rise* in a field with aggressive
  bettors → variety is self-reinforcing (a reason to build the aggressive tendencies too).

### Give-up turn / one-and-done — BUILT + PRICED (2026-05-29)

The **second leak**, and the first that closes a full loop: it is the **dual of the
multistreet H1 barrel** (already `built✅`). H1 *pumps* turn bet frequency for the
thin/semi-bluff classes with initiative; give-up-turn *dampens* it — the "no second
barrel" player c-bets the flop then checks back everything that isn't strong value on
the turn. The exploiter (**float flop → steal turn**) is exactly the H1 barrel, so
attaching this leak to a personality hands that dormant skill a target.

Mechanism shipped (`spot_tendencies.py` `_give_up_turn` handler, reusing slow-play's
`_dampen_aggression` reshape; registered in `_RULE_IDS_BY_LAYER`; ablatable via
`--a-disable spot_tendencies:give_up_turn`; control = 100% NO_DIVERGENCE / +0.00,
verified; `test_strategy` green). **Gate:** turn-only, `has_initiative`,
`action_context == 'unopened'`, `hand_class ∈ {medium_made, weak_made, air_strong_draw,
air_no_draw}` — disjoint from slow-play (nuts/strong_made), so both can be configured
without conflict (unit-tested). Priced on the same TAG carrier (cap 0.30), strength 0.8,
24k HU:

| Opponent | give-up-turn price (bb/100) | verdict |
|---|---|---|
| Baseline (self-play, **intrinsic**) | **−1.47** [−4.13, +1.18] | **free** — CI∋0 |
| jeff (over-folder) | **−1.54** [−3.31, +0.24] | **free** — CI barely ∋0 |
| punisher (reg) | **+0.14** [−1.21, +1.48] | **free** — CI∋0, dead neutral |

- Fires on ~1% of hands; **every** diverging node is a `turn|…` spot (gate is exact),
  cost spread thin across 122 nodes self-play (largest single-node −0.77 → no
  concentration) → the cheap-variety signature.
- **Verdict: FREE variety** — even cheaper than slow-play (which cost −5.13 vs jeff).
  Why cheaper: the give-up classes are the *thin* part of the range where the chart bets
  least to begin with, so abandoning the barrel forgoes little realized value in the
  current field. Shippable.
- **Same self-reinforcing finding:** in a homogeneous field nobody punishes a checked
  turn (the over-folder takes a free card; the reg doesn't stab). The leak's cost would
  *rise* against floaters/turn-stabbers — and its exploiter (multistreet H1) is already
  built, so this is the cleanest leak↔exploiter loop to demo: attach give-up-turn to one
  personality, turn on H1 for another, and the second extracts from the first.

### Fit-or-fold + auto-c-bet — BUILT + PRICED, with a methodology finding (2026-05-29)

Two more handlers (`_fit_or_fold`, `_auto_cbet`) on two new bounded reshapes
(`_pump_fold` = non-fold mass → fold; `_pump_aggression` = check/call mass → bet, the
inverse of `_dampen_aggression`). **fit_or_fold:** flop, `facing_bet`, `{weak_made,
air_no_draw}` → over-fold the air the chart floats. **auto_cbet:** flop, `unopened`,
initiative, thin classes → c-bet the checking range (the flop dual of give-up-turn).
Both default OFF; 41 spot-tendency tests green. Priced on the TAG carrier (both on,
disable one per arm to isolate), 24k HU:

| Tendency | self-play (intrinsic) | jeff (over-folder) | punisher (reg) |
|---|---|---|---|
| fit_or_fold | +1.71 [−0.30, +3.72] | +0.28 [−0.32, +0.87] | **+1.89 [+0.79, +2.99]** |
| auto_cbet | +0.34 [−3.96, +4.65] | +1.30 [−0.95, +3.54] | +0.08 [−1.05, +1.20] |

**Both price free — even mildly +EV — and that is the finding, not a win.** A leak that's
EV-neutral is *not exploitable*, which is the catalog's whole point (the loop + the
human-learnable counter). Why they came out free:
- **fit-or-fold (free*):** in **HU**, folding pure air/weak to a single flop c-bet is ~the
  correct play (your equity is low, you have no initiative, floating needs later barrels to
  pay) — vs the aggressive reg it's even CI-clear +EV (you stop paying off his barrels). The
  textbook fit-or-fold leak bites when you fold hands **with equity/playability** (2nd pair,
  draws) or **multiway / vs a floatable player** — none of which my narrow `{weak_made,
  air_no_draw}` HU gate captures. So "barrel relentlessly" has nothing to punish: the folds
  are correct.
- **auto-c-bet (free*):** HU c-bet ranges are already very wide, so betting the marginal
  checking range is ~EV-neutral. Its *exploitability* doesn't live in the flop bet (free) —
  it lives in the **follow-through**: an auto-c-bettor who then abandons the turn is the
  textbook "one-and-done," i.e. **auto_cbet + give_up_turn composed** (disjoint streets, so
  they stack on one personality). Alone, auto-c-bet is just free flavor.

**Open design question (the reason to pause — see "Roadmap / decision" below):** the cheap,
*correct-spot* version of a leak is recognizable flavor but creates **no exploitable tell**.
Making these genuine loop-closing leaks means deliberately gating them onto **−EV spots**
(fit-or-fold also folding `medium_made`/`air_strong_draw`; the HU-regime caveat; etc.). That
is a philosophy call — "free recognizable flavor" vs "priced, exploitable, teachable leak" —
and it recurs for every remaining catalog leak, so it's worth settling before building more.

## Tendency & skill catalog (running list — single source of truth)

This is a **symmetric skill system** with three move-types; a bot is composed from a
menu of them, which is what makes the skill gradient:
- **Leak** — a suboptimal spot tendency (the exploitable side; variety / weaker bots). This
  session's `spot_tendencies` layer.
- **Adaptive / exploiter** — *detect* an opponent's leak (via `OpponentModelManager` stats)
  and apply the counter. `exploitation.py` + the multistreet barrel + the value overbet.
- **Defense** — stay unexploitable in a spot (frequency guard). The river bluff guardrail.

**Leaks and exploiters are duals:** every leak has a detector that punishes it, and several
already-built exploiters were parked only because the homogeneous field gave them no target.
Adding the leak lights up the exploiter — *and* gives a human a learnable counter. Sourced
from poker pedagogy (Upswing, Range Craft, PokerVIP, MyPokerCoaching) + our own measured work.

Status legend — leak: `shipped` / `priced` / `backlog`; exploiter: `built✅` / `partial` / `parked` / `—`.

| Leak (tendency) | Trigger spot | Exploiter (adaptive counter) | Leak | Exploiter |
|---|---|---|---|---|
| slow-play / trap | strong made + initiative, unopened, flop/turn | value-bet thin vs the trapper | **priced (free)** | — |
| give-up turn (one-and-done, no barrel) | turn, initiative, checked to | float flop → steal turn | **priced (free)** | **built✅** (multistreet H1) |
| over-fold to 2nd barrel | turn facing bet, marginal made | double-barrel | backlog | partial (multistreet H2, off) |
| fit-or-fold / over-fold to c-bet | flop facing c-bet, air | barrel relentlessly | **priced (free*, see below)** | partial (`exploitation.py`) |
| auto-c-bet (c-bets 100% w/ initiative) | flop, initiative, unopened | float / raise their c-bets | **priced (free*, see below)** | — |
| under-bluff river (no triple barrel) | river, air, as bettor | over-fold their river bets; call their turn bets | backlog | — |
| sticky / pays off (can't fold) | facing river bet/raise, weak made | value-bet thin + overbet, never bluff | (≈station) | **built✅** (overbet, +42 vs payers) |
| over-bluff (too many bluffs) | river, air, as bettor | over-call bluff-catchers | backlog | **built✅ as defense** (river guardrail) |
| face-up sizing (big=strong, min-raise=nuts, overbet=nuts) | any bet node; strength→size | read size → call/fold | backlog (**strategic**) | parked (sizing-aware D1) |
| over-fold to 3-bet | preflop facing 3-bet | 3-bet wide as a bluff | backlog | — |
| face-up / nitty 3-bet (value only) | preflop 3-bet decision | fold to their 3-bets, stop paying | backlog | — |
| open-limp | preflop RFI | iso-raise wide | backlog | — |
| donk-when-weak / tiny donk | OOP lead, weak | raise it | backlog | — |
| position-blindness (plays OOP like IP) | OOP nodes | attack the overplays | backlog | — |

**Priority:** the leaks whose exploiter is already `built✅` close a full loop immediately
(add the leak → a dormant skill gets a target → human gets a learnable counter). **give-up-turn
(dual of the multistreet H1 barrel) is now priced free + shipped** ✅. Next top-of-list:
**fit-or-fold / over-fold-to-c-bet** (classic, very readable; exploiter `partial` in
`exploitation.py`) and **sticky/pays-off** (exploiter `built✅` as the value overbet). Each leak
still priced + budgeted before shipping; preflop leaks need the layer wired into the preflop path
(slow-play + give-up-turn are postflop-only today).

### Ownership (updated 2026-05-29)
The parallel exploitation session has **wrapped** — `exploitation.py` / `OpponentModelManager`
(the detector + defense half) and the `spot_tendencies` layer (the leak half) are now **both
ours**. So we can build full leak↔exploiter loops end-to-end: add a leak, then verify/tune the
detector that punishes it, in one pass. (Earlier drafts of this doc told us to sync with the
parallel session before touching the reader side — that constraint is gone.)

## Handoff pointers

- Postflop forward plan + ruled-out frontier: `docs/plans/POSTFLOP_NEXT_LEVER.md`.
- Sizing-aware scope (parked, revived by variety): `docs/plans/SIZING_AWARE_OPPONENT_MODELING.md`.
- Full session narrative (wrong turns + corrections): `docs/captains-log/lookup-tables/eval-harness-and-exploitation.md`.
- Ownership: the parallel session wrapped 2026-05-29 — `OpponentModelManager`/`exploitation.py` (detectors/defenses) AND the deviation system + `spot_tendencies` + attribution gate are now all ours; build leak↔exploiter loops end-to-end. The `--a-hero/--b-hero` and `--a-disable/--b-disable` gate extensions are additive (byte-identical when unset).
