---
purpose: Methodology + handoff for pricing the tiered bot's personality deviations (EV cost of non-max-EV play) and using that to add bounded, characterful variety to the AI field
type: guide
created: 2026-05-28
last_updated: 2026-05-28
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

## Current state (handoff — what's true as of 2026-05-28)

**Branch `lookup-tables`** (merged with `origin/development`, pushed). All Python
runs in Docker: `docker compose exec -T backend python ...`.

**Shipped this session (production gameplay changes, all eval-validated):**
1. **Wider late-position RFI** (`4f5fb311`, pre-session) — CO/BTN/SB GTO-shaped opens.
2. **Multistreet flop+turn barrel-continuation** (`d1781b30`) — `enable_multistreet_context=True`, `multistreet_h1_streets={FLOP,TURN}` (river leg dropped, measured −EV), H2 off. +3–12 bb/100 vs realistic opponents.
3. **Value overbet** (`170a86ac`) — `enable_overbet_context=True`, `overbet_size=150`, classes `{nuts,strong_made}`, streets `{TURN,RIVER}`. **The big one: +40 HU / +77 6-max cumulative vs former self, no regression** (`2329d0eb`).

**Key measured findings (don't re-litigate):**
- The cheap chart frontier (frequency, sizing granularity, dimensional coverage) is **tapped**; the remaining strength lever is the **parked solver program** (HU/multiway, expensive).
- The value overbet is **field-dependent**: +42 vs payers, **−24 vs a perfect sizing-reader** (D1 oracle). It's +EV vs the realistic non-sizing-reading field but not robust.
- Sizing-aware opponent modeling (`docs/plans/SIZING_AWARE_OPPONENT_MODELING.md`) is **scoped but parked**: the field doesn't read/exhibit sizing, so the machinery is inert — *until variety creates exploitable tells* (see "strategic payoff" below).

**The personality-deviation system (the thing we're pricing):**
- `poker/strategy/personality_modifier.py` — `modify_strategy(base, anchors, emotional_state, deviation_profile)` distorts the baseline chart in logit space, **bounded** by `max_kl` / `max_per_action_shift`.
- `poker/strategy/deviation_profiles.py` — `DEVIATION_PROFILES`: **`nit, rock, tag, calling_station, lag, maniac`**. Axes: `aggression_scale`, `looseness_scale`, `risk_scale`, `ego_fold_penalty`, + the KL bounds.
- Sim wiring: `simulate_bb100.make_controller` sets `controller._deviation_profile = DEVIATION_PROFILES[profile_key]` (None for `Baseline`, which sets `skip_personality_distortion=True`). `ARCHETYPES[name]` carries `{kind, profile, anchors}`.

**The eval gates (the pricing instruments):**
- `experiments/ab_node_attribution.py` — **paired-CRN first-divergence per-node attribution** (the primary pricing tool). Already supports `--a-mode/--b-mode` (multistreet), `--overbet-a/-b`, `--adaptive-opp` (D1 oracle), `--h1-streets`, `--heads-up`, `--stack-bb`. **Extension needed for this program: `--a-hero/--b-hero`** (per-arm hero archetype) — see methodology.
- `experiments/measure_passivity.py` — Tier-A diagnostics + `--leak-report`.
- `experiments/champion_challenger.py`, `experiments/sng_runner.py`, `experiments/exploit_bb100.py` — other gates (parallel session's; coordinate).

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

**Required extension (build before running):** `ab_node_attribution` currently
takes a single `--hero` for both arms. Add `--a-hero` / `--b-hero` overrides
(default to `--hero`), resolved per arm into `ARCHETYPES[...]` → distinct
`config_arch` passed to each arm's `_run_one_hand`. Same seat name (opponents/deck
identical), different deviation profile. Mirrors the `--a-mode/--b-mode` pattern.
Control: `--a-hero Baseline --b-hero Baseline` MUST be 100% NO_DIVERGENCE / +0.00.

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

### Results (done 2026-05-28, 12k HU each — self-play PRIMARY + jeff field slice; punisher TBD)

| Profile | **vs Baseline (self-play, INTRINSIC — primary)** | vs jeff (over-folder slice) | vs punisher | Verdict (budget) |
|---|---|---|---|---|
| nit | **+6.45** [−4.2, +17.1] | −5.79 [−9.8, −1.8] | _TBD_ | **free** — tight is safe vs competent; CI∋0 |
| rock | **+4.25** [−6.1, +14.6] | −6.21 [−10.2, −2.3] | _TBD_ | **free** — CI∋0 |
| tag | **+0.26** [−10.5, +11.0] | +3.73 [−1.8, +9.2] | _TBD_ | **free** — near-GTO, ~0 as expected |
| lag | **−7.05** [−22.9, +8.8] | +7.20 [+0.25, +14.2] | _TBD_ | **priced** — modest spew vs competent; CI∋0 |
| calling_station | **−10.26** [−19.7, −0.8] | −4.95 [−8.7, −1.2] | _TBD_ | **priced** — CI-clear; pays off value (FLOP −5.2) |
| maniac | **−24.14** [−45.9, −2.4] | +9.94 [+0.73, +19.2] | _TBD_ | **expensive / borderline-broken** — CI-clear, FLOP-concentrated (−16.4) → re-cap `max_kl` |

**Read — the self-play anchor inverted the jeff ranking (the whole reason to anchor on it):**
- **Intrinsic ranking (cheap→expensive):** Nit/Rock/TAG ≈ **free** (CI∋0, ~0 to +6) →
  LAG −7 / Calling Station −10 → **Maniac −24** (the costliest, CI-clear).
- **vs jeff was nearly the *opposite*:** Maniac read **best** (+9.94) but is the **worst**
  intrinsically (−24); Nit read **costly** (−5.79) but is **free** intrinsically (+6.45).
  jeff's number was *fish-exploitation*, not personality cost — pricing on jeff alone
  would have inverted the verdict. (Also: a 400h Nit self-play smoke read −50; the 12k
  run is +6.45 — the smoke was pure noise. Never read a 400h number.)
- **Budget verdicts:** Nit/Rock/TAG ship freely (cheap variety); LAG & Calling Station
  are priced flavor (acceptable, recognizable characters); **Maniac (−24, FLOP-concentrated)
  is over the budget → re-cap its `max_kl` to rein in flop over-aggression**, then re-price.
- **Caveat:** still the realistic-field price needs the **punisher (reg) vector** + a mix;
  self-play is the intrinsic ceiling-of-cost anchor, the field price sits between it and
  the (fish-flattering) jeff slice. 24k would tighten the wide CIs (LAG/Maniac span widely).

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

1. **Price the 6 profiles** (Exp 1) → audit: real variety vs broken vs accidental +EV.
2. **Re-cap `max_kl` per profile from the measured budget** (replace the guess).
3. **New spot/line-specific tendencies** (today's deviations are *global scalars*):
   sizing tells / face-up, slow-play/trap, donk-bet, open-limp, position-blindness,
   spot-specific over/under-bluffing. Each priced + budgeted before shipping.
4. **Close the loop:** with exploitable personalities in the field, re-judge the
   parked sizing-aware C (attack) + bluff-catch calibration — they now have targets.

## Handoff pointers

- Postflop forward plan + ruled-out frontier: `docs/plans/POSTFLOP_NEXT_LEVER.md`.
- Sizing-aware scope (parked, revived by variety): `docs/plans/SIZING_AWARE_OPPONENT_MODELING.md`.
- Full session narrative (wrong turns + corrections): `docs/captains-log/lookup-tables/eval-harness-and-exploitation.md`.
- Coordination: `OpponentModelManager`/`exploitation.py` is the parallel session's territory; the deviation system + attribution gate are this session's. The `--a-hero/--b-hero` extension is additive (byte-identical when both = `--hero`).
