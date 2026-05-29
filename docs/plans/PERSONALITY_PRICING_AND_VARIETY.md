---
purpose: Methodology + handoff for pricing the tiered bot's personality deviations (EV cost of non-max-EV play) and using that to add bounded, characterful variety to the AI field
type: guide
created: 2026-05-28
last_updated: 2026-05-29
status_note: "2026-05-29 — the variety-widening task is BUILT (hybrid width-tier tables + retuned distortion). See 'VARIETY WIDENING — BUILT' below; the older NEW-CONTEXT callout that posed it as open is left for narrative."
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

> **NEW-CONTEXT START HERE (updated 2026-05-29 — read this first).**
>
> **The live frontier is making the variety VISIBLE.** The spot-tendency leak system
> (9 leaks built + priced) and the adaptive-exploit side are DONE and explored — do
> not re-open them (see "what's settled" below). The audit that matters: the 6
> archetypes **barely play differently** at the table. Measured (`measure_passivity
> --hero <archetype> --opponents gto --hands 2500`), same opponents:
>
> | archetype | VPIP | PFR | postflop AF |  | archetype | VPIP | PFR | AF |
> |---|---|---|---|---|---|---|---|---|
> | Nit | 22% | 21% | 0.19 |  | Calling Station | 26% | 23% | 0.22 |
> | Rock | 22% | 21% | 0.20 |  | LAG | 27% | 25% | 0.36 |
> | TAG | 23% | 22% | 0.31 |  | Maniac | 32% | 31% | 0.48 |
>
> A nit and a maniac differ by 10 pts of VPIP (a real field is ~12%→~55%); **PFR≈VPIP
> for everyone → nobody limps/calls**, so the "Calling Station" *doesn't call* (26/23).
> The variety is nominal because the priced-"free" deviations are too small to move
> aggregate stats — **free = invisible.**
>
> **THE TASK:** widen the per-archetype deviation `aggression_scale`/`looseness_scale`
> + `max_per_action_shift` (in `poker/strategy/deviation_profiles.py`) until this
> VPIP/PFR/AF table looks like a real field, and **re-shape `calling_station`** to move
> preflop raise→call + postflop fold→call (high VPIP, low PFR, high WtSD — a real
> caller, not just "looser"). **Acceptance test = re-run that table until the archetypes
> are visibly distinct.** Variety *costs EV* and that's correct — a wild bot SHOULD
> bleed (the bleed is the skill gradient); use the pricing gate to keep it bounded, but
> budget weak characters generously, not ~0. Optional fan-out: sweep candidate
> scale-sets via parallel `measure_passivity` runs (workflow-shaped) then eyeball-pick.
>
> The strength-side gameplay layers (1–3 below) are done — don't re-open them.

> **ARCHITECTURE for the widening task — read before you crank caps.** Don't try to
> reach the loose archetypes by distortion alone: it has a **hard ceiling**. A bounded
> logit offset **cannot open a hand the base chart folds ~100%** (no raise/call mass to
> amplify; the per-action cap keeps it ~0). This is a prior measured finding on this
> project ("offsets can't open fold-1.0 hands → table-selection"), and it's exactly why
> the audit's Maniac capped at **32% VPIP, not 55%**. So the decision splits:
> - **Tightening (Nit/Rock):** distortion works — you can always shift toward fold. No new table.
> - **Loosening (LAG/Maniac/Station/loose personas):** needs a **wider base TABLE** — distortion
>   can't get there. Tables already exist: `preflop_100bb_6max_tight_rfi.json` /
>   `_6max.json` (standard) / `_wider_rfi.json`; add an even-wider maniac/station chart only
>   if `wider_rfi` isn't loose enough (the acceptance-test table decides).
> - **Postflop (sticky/aggression/the leaks):** distortion works — reshapes move mass between
>   *existing legal* actions (fold→call etc.), so no new postflop tables. (sticky already did this.)
>
> **Architecture = a few (~3–4) width-tiered preflop tables SELECTED by archetype tier, +
> the existing distortion on top for fine personality + the spot tendencies.** NOT 62
> per-personality tables (unmaintainable / expensive to solve). NOT pure distortion (can't
> reach the loose end). Tables carry the coarse VPIP *envelope*; distortion carries the flavor
> *within* it. **Wiring needed:** table-selection is currently by stack (`depth_strategy_tables`)
> and field size (`hu_strategy_table`) only — **add an archetype→width-tier map** alongside those
> (small change in the controller's table picker + `make_controller`). Then widen scales/caps for
> the per-archetype flavor and iterate the VPIP/PFR/AF table.

> **VARIETY WIDENING — BUILT (2026-05-29).** The hybrid architecture above is now
> implemented and the acceptance test passes. **Answer to the framing question
> ("a few tuned tables vs. the adjustment system"): BOTH — and they do different
> jobs.** Width-tier preflop TABLES carry the coarse VPIP *envelope* (distortion
> has a hard ceiling — it can't open hands the base chart folds ~100%); the
> distortion layer carries the *flavor within* (aggression/passivity character +
> the tight-end separation, which distortion CAN reach since it can always boost
> fold). Confirmed empirically: table-only envelopes (Baseline hero, no distortion)
> = tight 21% VPIP / std 25% / **loose 50%** / **station 43% VPIP, 19% PFR** —
> distortion alone topped out ~32–43% and could not make a caller.
>
> **What shipped:**
> - **3 new preflop tables** (`experiments/build_archetype_charts.py` →
>   `_loose.json`, `_loose_mid.json`, `_station.json`). Loose = wide RFI at every
>   position + wide continues (Maniac). Loose-mid = moderate RFI + continues,
>   landing between TAG and Maniac (LAG — textbook LAG is *not* as loose as a
>   maniac). Station = TIGHT RFI (opens few → low PFR, textbook ~8–12%) but heavy
>   flat-CALLING vs_open (fold→call) → a real caller. Tight (`_tight_rfi`) predates
>   this (Nit/Rock); TAG uses the standard base. **5 width tiers total:**
>   tight→Nit/Rock, std→TAG, loose-mid→LAG, loose→Maniac, station→Calling Station.
> - **Archetype→table wiring** (preflop-only; postflop stays on the base chart):
>   `ARCHETYPE_WIDTH_TABLE` + `select_deviation_profile_key` (deviation_profiles.py),
>   `load_archetype_preflop_tables` (strategy_table.py), `archetype_preflop_tables`
>   param + `_archetype_base_table` / `_select_preflop_table` (tiered_bot_controller.py),
>   wired in BOTH `flask_app/handlers/tiered_factory.py` (prod) and
>   `experiments/simulate_bb100.make_controller` (sims; `--preflop-chart` still wins
>   for Baseline-hero experiments — measure_passivity clears the hero's archetype
>   tables when a chart is forced).
> - **Retuned distortion** (deviation_profiles.py): tables now carry VPIP so the
>   scales carry flavor. Nit's 0.20 cap (which had throttled it to byte-identical
>   with Rock) → 0.30 + stronger looseness_scale so nit's tighter anchors express;
>   TAG aggression bumped for AF separation from Rock; station aggression_scale up
>   (raise→call) + high ego_fold_penalty (sticky/pays-off); maniac aggression_scale
>   2.0→2.2 for top-of-field AF (cap held at the priced 0.35).
>
> **Acceptance-test result** (`measure_passivity --hero <arch>` vs a 5×Baseline
> roster, 4500 hands; VPIP/PFR are ~opponent-independent so they anchor the spread).
> Last column = textbook 6-max VPIP/PFR bands — **all six match** and the ordering
> is textbook-correct (a station is looser than a LAG; clean passive/aggressive split):
>
> | arch | VPIP | PFR | gap | AF* | payoff | textbook VPIP/PFR |
> |---|---|---|---|---|---|---|
> | Nit | 15 | 10 | 5 | 0.36 | 60% | 12–16 / 8–13 ✅ |
> | Rock | 19 | 12 | 7 | 0.34 | 69% | 15–20 / 8–14 ✅ |
> | TAG | 24 | 20 | 4 | 0.70 | 80% | 18–24 / 15–20 ✅ (reg anchor, lower edge) |
> | LAG | 38 | 30 | 8 | 0.80 | 74% | 26–34 / 20–28 ✅ (top edge) |
> | Calling Station | 45 | 16 | **29** | 0.26 | 79% | 35–50 / 5–14 ✅ **the caller** |
> | Maniac | 56 | 48 | 8 | 1.25 | 59% | 50–75 / 35–55 ✅ |
>
> \* **AF here is NOT standard tracker AF.** This metric = aggressive / (check+call);
> PokerTracker AF = (bet+raise)/call (checks excluded), so these run lower and aren't
> a like-for-like with "TAG AF ≈ 2.5–3." Use the *ordering* (passives Nit/Rock/Station
> low, aggressives TAG/LAG/Maniac high) + the VPIP–PFR *gap* (the caller's 29 vs
> everyone else's ~5–8), which ARE standard.
>
> vs the doc's problem-state audit (Nit 22 → Maniac 32, a 10-pt spread, PFR≈VPIP,
> "station doesn't call"): now a **41-pt VPIP spread**, Nit≠Rock, a clean
> passive/aggressive split, and **the station calls**. `test_strategy` +
> `test_psychology_drift` green (1529 passed).
>
> **Tuning history (toward textbook):** v1 had LAG/Maniac both on the loose table
> (54/44 vs 56/48 — LAG was a maniac clone) and the station opening too much (PFR 19).
> Fixed by giving LAG its own **loose-mid** tier (→ 38/30, between TAG and Maniac) and
> giving the station a **tight RFI** (opens few → PFR 19→16) while keeping its wide
> flat-call vs_open. The hybrid lever (a better table) is what closed both gaps —
> distortion couldn't. Finally pulled **TAG down to the lower edge** (26→24/20) by
> raising its looseness_scale 0.6→1.6 (its negative looseness deviation boosts fold →
> tightens entry; aggression_scale 1.6 stays for the AF flavor): TAG is the
> competent-reg anchor, so it should sit at the bottom of its band, not over the top —
> this sharpens the gradient (TAG = the standard the loose archetypes are measured
> against) and widens the gap to LAG. **Design principle (the "upper vs lower bound"
> call): push the spread — weak/characterful archetypes (Station/Maniac) lean to the
> LOOSE edge (more exploitable, more action, the EV bleed is the intended handicap);
> the competent reg (TAG) leans TIGHT (near-optimal, no un-intended leak). Looser also
> means more multiway pots, which mute postflop skill — another reason not to loosen
> the strong end.**
>
> **Caveats / open follow-ups (NOT done):** (1) The `measure_passivity` bb/100 vs a
> 1-vs-5-Baseline table is a roster artifact (over-rewards aggression, punishes the
> caller), NOT the canonical price — re-price the table+distortion combo with the
> paired-CRN gate (`ab_node_attribution` would need archetype-table wiring like its
> `--a-hero/--b-hero`). Variety is *expected* to cost EV (the bleed is the skill
> gradient), so the station's bleed is by design, but it's unpriced. (2) LAG/Maniac
> still separate partly on aggression; LAG VPIP (38) is ~4pts over the textbook
> ceiling — fine. (3) Depth (50/25bb) + HU charts are still width-tier-agnostic (100bb).

**Shipped this session (production gameplay changes, all eval-validated):**
1. **Wider late-position RFI** (`4f5fb311`, pre-session) — CO/BTN/SB GTO-shaped opens.
2. **Multistreet flop+turn barrel-continuation** (`d1781b30`) — `enable_multistreet_context=True`, `multistreet_h1_streets={FLOP,TURN}` (river leg dropped, measured −EV), H2 off. +3–12 bb/100 vs realistic opponents.
3. **Value overbet** (`170a86ac`) — `enable_overbet_context=True`, `overbet_size=150`, classes `{nuts,strong_made}`, streets `{TURN,RIVER}`. **The big one: +40 HU / +77 6-max cumulative vs former self, no regression** (`2329d0eb`).
4. **Spot-tendency variety system (item 3)** — `poker/strategy/spot_tendencies.py` (`apply_spot_tendencies`, general layer) + slow-play leak (priced **free**) + per-personality override hook (`spot_tendencies` key in personalities.json → `TieredBotController.deviation_profile` merge) + the `--a-disable/--b-disable` pricing-gate flag. Defaults OFF. Commits `bdf150fe`/`3973ab25`/`1f63f658`/`ba98183a`. See the catalog for what's next.
5. **Give-up-turn leak (2026-05-29)** — second `spot_tendencies` handler (`_give_up_turn`), the **dual of the multistreet H1 barrel** (first leak whose exploiter is already built). Priced **free** (intrinsic −1.47, jeff −1.54, punisher +0.14; all CI∋0). Turn-only, disjoint from slow-play by hand class. See "Give-up turn" subsection below.
6. **Fit-or-fold + auto-c-bet leaks (2026-05-29)** — `_fit_or_fold` / `_auto_cbet` on two new bounded reshapes (`_pump_fold`, `_pump_aggression`). Both priced **free/+EV** — and that surfaced a methodology finding: a *correct-spot* leak is recognizable flavor but **not exploitable**, so it doesn't close the loop. See "Fit-or-fold + auto-c-bet" subsection + the open design question.
7. **Sticky/pays-off + over-bluff + under-bluff leaks (2026-05-29)** — `_sticky` (`_dampen_fold`) priced **−1.87/−0.46/−0.26 (CI-clear −EV everywhere)** → the first real "skill"-tier leak, the payer the value overbet targets. `_over_bluff` (`_pump_aggression`) free intrinsically, −EV vs callers. `_under_bluff` (`_dampen_aggression`) the over-bluff dual: free/+EV, a style/face-up leak. See "Sticky + over-bluff" subsection.
8. **`--hero-spot-tendency` + `--opp-spot-tendency` gate flags (2026-05-29)** — price a spot tendency on hero or opponent without editing a deviation profile in source. Retires the "carrier" hack. Validated (control = 100% NO_DIVERGENCE). Plus a `tag` opponent roster.
9. **over-fold-2nd-barrel + donk-when-weak leaks (2026-05-29)** — signal-plumbed (`facing_double_barrel`, `position`). Both free; over-fold-2nd-barrel too rare to price in HU. **9 leaks total now.**
10. **Multiway pricing gate (2026-05-29, DONE)** — validated; **refuted** the "fit-or-fold is a hidden multiway leak" hypothesis (still +3.90 in 6-max). `docs/plans/MULTIWAY_PRICING_GATE.md`.
11. **Leak × counter matrix + floor investigation (2026-05-29)** — a leak's cost is a **matchup** (over-bluff −7.6 vs a caller; sticky ±2 by opponent), not a number. Over-fold leaks are floor-capped (`docs/plans/FLOOR_DEFEATING_LEAKS.md`). See those subsections.
12. **Adaptive overbet / detector (2026-05-29) — built, MEASURED, SHELVED as prospective.** Doesn't fire (gates on the safety-dampened `value_vs_station` signal → ~0 vs tight fields) AND has no target (no sizing-reader in the field). Default OFF, harmless. Real fix + why it's prospective in the "Adaptive detector" subsection. **Do not re-open the AI-vs-AI exploit side** — settled with the user.
13. **Personality wiring + variety audit (2026-05-29)** — 3 exemplars wired (`docs/plans/PERSONALITY_LEAK_WIRING.md`); audit found the archetypes barely differ → **the variety-widening task is now the frontier (see NEW-CONTEXT callout above).**

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
- For pricing a **spot tendency** (CLEAN RECIPE as of 2026-05-29): `--hero-spot-tendency name:strength` configures the tendency on the hero (both arms) with **no source edits** (it sets the controller's `_spot_tendencies_override`), and `--a-disable spot_tendencies:<name>` turns it OFF on arm A. The paired delta (B−A) is the tendency's marginal cost. Requires a non-Baseline `--hero` (e.g. `TAG`; Baseline skips the personality layer). Example: `... baseline 3000 $SEEDS --hero TAG --hero-spot-tendency sticky:0.8 --a-disable spot_tendencies:sticky --heads-up`. Control = `--a-disable ... --b-disable ...` (same on both) → 100% NO_DIVERGENCE / +0.00. **This retires the old "carrier" hack** (temporarily editing `DEVIATION_PROFILES[...]` in source — fragile, easy to forget to revert, collides across parallel sessions). `--a-disable/--b-disable` alone (BUILT 2026-05-29) still toggles any layer-rule per arm.
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
  textbook fit-or-fold leak supposedly bites when you fold hands **with equity/playability** (2nd
  pair, draws). I'd guessed that (and "or multiway") was the missing piece — **both refuted**: the
  multiway gate showed it free in 6-max, and *widening the gate to fold equity hands ALSO stayed
  free* (see the WIDENING note below). In HU/6-max over-folding to a c-bet is just cheap. So
  "barrel relentlessly" has nothing to punish: the folds are ~correct, at any gate width.
- **auto-c-bet (free*):** HU c-bet ranges are already very wide, so betting the marginal
  checking range is ~EV-neutral. Its *exploitability* doesn't live in the flop bet (free) —
  it lives in the **follow-through**: an auto-c-bettor who then abandons the turn is the
  textbook "one-and-done," i.e. **auto_cbet + give_up_turn composed** (disjoint streets, so
  they stack on one personality). Alone, auto-c-bet is just free flavor.

**Open design question (the reason to pause — see "Roadmap / decision" below):** the cheap,
*correct-spot* version of a leak is recognizable flavor but creates **no exploitable tell**.
Making these genuine loop-closing leaks means deliberately gating them onto **−EV spots**
(fit-or-fold also folding `medium_made`/`air_strong_draw`; etc.). That is a philosophy call —
"free recognizable flavor" vs "priced, exploitable, teachable leak" — and it recurs for every
remaining catalog leak.

**RESOLVED for the regime half (multiway gate, 2026-05-29):** the hypothesis that fit-or-fold/
auto-c-bet are real leaks our HU instrument just can't see is **REFUTED**. A delegated agent
built + validated a 6-max pricing path (just omit `--heads-up`; control = 100% NO_DIVERGENCE,
residual exactly +0.000; full runbook in **`docs/plans/MULTIWAY_PRICING_GATE.md`**) and
re-priced them: `fit_or_fold` is **+3.90** [+1.17, +6.63] self-play in 6-max (still free/+EV),
`auto_cbet` **+3.18**. So the non-exploitability is the **gate's narrowness, not the regime** —
the layer fires only on `{weak_made, air_no_draw}`, which are fold-profitable everywhere; the
textbook leak needs folding hands *with equity* (draws/2nd pair), which the gate excludes. The
multiway gate also characterized the **suppression interaction** (`multiway` step 4 runs before
`spot_tendencies` step 6.b): slowplay exempt, give_up_turn partially eaten, auto_cbet
antagonistic (undoes suppression), fit_or_fold orthogonal — and prices the composed behavior
correctly. **Takeaway (then):** to make fit-or-fold exploitable, *widen its hand-class gate* —
don't chase the regime.

**WIDENING TRIED — and it did NOT make fit-or-fold −EV (2026-05-29).** Per the above, widened
`_FITFOLD_CLASSES` to `{medium_made, weak_made, air_strong_draw, air_no_draw}` (fold everything
but strong made — incl. 2nd pair + draws, the equity hands). Re-priced 24k HU: **+1.61** [−1.67,
+4.89] self-play, **+0.40** vs jeff, **+0.81** vs punisher — **still free** (CI∋0 everywhere).
That's now **three concordant measurements** (narrow HU +1.71, narrow 6-max +3.90, widened HU
+1.61). The equity hands *do* over-fold (they show up as diverging nodes), but folding them to a
flop c-bet is ~EV-neutral in HU/6-max: a 2nd pair or a draw facing a c-bet has to survive turn +
river barrels to realize, so folding the flop ≈ folding the turn in EV — the chips "lost" by
folding equity ≈ the chips "saved" by not paying barrels (per-node contributions are mixed-sign,
small, netting ~0). **Conclusion: "fit-or-fold" is a *full-ring* leak intuition; in HU/6-max
over-folding to a c-bet is structurally cheap and fit-or-fold is a STYLE leak, not a skill one —
the gate width was never the lever.** The widened gate is KEPT (it folds a wider, more
visibly-weak-tight range → a better *style* leak) but it does not close the "barrel relentlessly"
loop. To force a real over-fold leak you'd have to defeat the bot's own **math/defense floors**
(they run after the spot layer and re-add pot-odds-mandated calls — the structural reason over-fold
leaks self-limit to near-neutral folds), which is a larger change, not a gate tweak. The philosophy
fork (free-flavor vs engineered-−EV) thus narrows: **on early streets, "free flavor" may be the
only honest option short of disabling the floors.**

### Sticky/pays-off + over-bluff — BUILT + PRICED, the first real −EV leaks (2026-05-29)

After fit-or-fold/auto-c-bet priced free-but-inert, these two were chosen *because*
their exploiters are already built and they should price as genuine −EV — the "skill"
tier (weaker bots a built exploiter and a human can punish), vs the "style" tier (the
free flavor). Both reuse the bounded-reshape pattern: `_dampen_fold` (fold mass → call)
and `_pump_aggression` (the over-bet reshape, shared with auto-c-bet). Priced via the new
`--hero-spot-tendency` flag (no source edits), 24k HU:

| Tendency | Trigger | self-play (intrinsic) | jeff (loose station) | punisher (reg) |
|---|---|---|---|---|
| **sticky** | river, facing bet/raise, weak/medium made | **−1.87 [−2.58, −1.16]** | **−0.46 [−0.79, −0.12]** | **−0.26 [−0.48, −0.05]** |
| **over_bluff** | river, unopened, air | +0.39 [−0.83, +1.61] | **−1.44 [−2.88, −0.00]** | −0.64 [−1.76, +0.49] |

- **sticky is the first CI-clear −EV leak across the board** (and the cost profile is
  poker-coherent: *worst* vs the balanced Baseline that value-bets a full range; *least-bad*
  vs the aggressive reg, because crying-calling also catches *his* bluffs, offsetting the
  value he gets paid). It is exactly the **payer the +42 value-overbet exploiter targets** —
  attach sticky to a personality and the overbet bot extracts from it. Fires on ~0.25% of
  hands (the river bluff-catch spot is rare), so the per-hand cost is large but the bb/100 is
  modest — that's correct, not weak.
- **over_bluff is free intrinsically but −EV vs a caller** (−1.44 vs jeff, whose WtSD=0.59
  means he calls down and punishes river bluffs — a live bluff-catcher doing the exploiter's
  job). ~free vs the reg (punisher folds the right amount). Its EV would go **+** vs a true
  over-folder (fold equity) and more **−** vs a dedicated bluff-catcher — the field-dependence
  a leak should show. Note jeff is mislabeled "over-folder" in the methodology section above;
  its stats (vpip 0.39, WtSD 0.59, fold_to_cbet 0.45) are *flop* over-fold + *river* station.
- **Both are the "skill" tier:** recognizable AND punishable, with built exploiters (sticky →
  value overbet; over-bluff → over-call / the river guardrail is its defensive dual). Shippable
  as real weak-spots in the gradient.

**under-bluff (the over-bluff dual, same spot opposite direction)** — river, unopened, air →
*dampen* bet (never bluff). Priced **+0.72 self-play / +1.67 vs jeff / +0.04 vs punisher** —
free-to-+EV, because not-bluffing-a-caller is actually *correct* (CI-clear +1.67 vs the station).
So it's a **style/face-up** leak, not a −EV one: it doesn't cost bb/100, but it's *readable* —
"when they bet the river it's always value," and the human (or a detector) counters by
over-folding to their river bets. The over-bluff/under-bluff pair is a clean illustration that a
tendency's *EV sign flips with the field* (over-bluff −EV vs the caller, under-bluff +EV vs the
same caller) while its *recognizability* is constant — the two axes (priced vs readable) made
concrete.

### over-fold-2nd-barrel + donk-when-weak — BUILT + PRICED (2026-05-29, signal-plumbed)

Required threading two signals into the spot layer (`facing_double_barrel` from `derive_signals`;
`position` from `node.position`). Both priced free, 24k HU:

| Tendency | self-play | jeff | punisher | note |
|---|---|---|---|---|
| donk_when_weak | −0.58 [−5.55,+4.38] | −0.87 [−2.00,+0.26] | −0.31 [−1.70,+1.09] | free; fires ~5–7% (OOP lead) |
| over_fold_2nd_barrel | (too rare) | +0.03 [−0.01,+0.07] | +0.29 [−0.54,+1.11] | **near-zero fire rate even vs barrelers** |

- **donk-when-weak** is a clean **style** leak: leading weak OOP into the aggressor is ~EV-neutral
  in HU (you have fold equity; the checked line often faces a bet anyway), but it's *recognizable*
  (a face-up weak donk) and the human counter is "raise it."
- **over-fold-2nd-barrel can't be priced in HU** — the spot (opp bets flop AND turn, hero holding
  exactly marginal made facing it) fires on ~0% of HU hands even vs the aggressive reg (≈25 hands
  in 24k). What little fires is free. It's an over-fold leak (so cheap + floor-capped like
  fit-or-fold) AND too rare for the HU instrument; it may matter in full-ring, but we can't measure
  it here. Kept (built, OFF) but flagged unmeasurable-in-HU.

### Working principle (LOCKED 2026-05-29): early-street = style, river/commit = skill

Across nine priced leaks a clean pattern holds: **fold-frequency and bet-frequency leaks on the
flop/turn price free** (auto_cbet, fit_or_fold even widened, donk, under_bluff, over_fold_2nd_barrel,
give_up_turn) — HU ranges are wide, marginal hands face later barrels, and the math/defense floors
re-add odds-mandated calls — so they are the **style tier** (recognizable, not −EV, not strongly
exploitable). The leaks that price **CI-clear −EV are all river / chip-commit spots** (sticky −1.87;
over_bluff −EV vs callers) — the **skill tier**. Takeaway for future leaks: *don't expect an
early-street frequency tendency to be a skill leak in HU; build those for flavor, and look to the
river / committed pots (or the floor-defeating path, under investigation) for −EV.*

### Leak × counter matrix — a leak's cost is a property of the MATCHUP, not the leak (2026-05-29)

Motivated by the question "are the *free* leaks actually exploitable if an opponent plays the
counter-strategy?" Priced each leak vs the fixed opponent whose strategy should punish it (24k HU,
`--hero-spot-tendency X --a-disable spot_tendencies:X`, opponent roster = the counter):

| Leak | vs neutral field (self/jeff/punisher) | vs its counter-strategy | read |
|---|---|---|---|
| fit_or_fold | +1.71 / +0.28 / +1.89 | **maniac (barreler) +2.68** [−1.09,+6.46] | still free |
| auto_cbet | +0.34 / +1.30 / +0.08 | **LAG (floater) +0.23** [−3.95,+4.41] | still free |
| donk_when_weak | −0.58 / −0.87 / −0.31 | **maniac (raiser) +1.09** [−4.93,+7.12] | still free |
| slowplay | ~0 / −5 / −2 | **maniac (aggressor) −1.62** [−6.70,+3.46] | still free |
| over_bluff | +0.39 / −1.44 / −0.64 | **station (caller) −7.60** [−8.96,−6.24] | **CI-clear −EV** |
| sticky | **−1.87** / −0.46 / −0.26 | **maniac (bluffer) +2.12** [+0.31,+3.93] | **CI-clear +EV** |

**Three results that reframe the whole program:**

1. **CORRECTION — the −10 was a mirage.** A 2-seed smoke read fit_or_fold at −10.14 vs the maniac
   (CI [−20,−0.04], touching 0); the 24k says **+2.68, free**. Never trust a 2-seed number — the
   project's load-bearing lesson, caught again. The early-street leaks (fit_or_fold, auto_cbet,
   donk, slowplay) **stay free even vs the opponent built to punish them.**

2. **Early-street leaks aren't extractable by a fixed counter** — two reasons, both confirmed by the
   floor investigation (below): (a) the `defense_floor` runs *after* the leak and re-adds the
   over-folded calls; (b) a caricature aggressor (ManiacBot/LAG) barrels its *whole* range, not
   *surgically* the leak's spot, and the leak fires on only ~0.3–3.5% of hands. Loosening the
   attacker (ManiacBot is ~unclamped) does nothing without surgical targeting.

3. **River/commit leaks ARE matchup-sensitive — the sign flips with the opponent.** `over_bluff`
   (the bluffer) is **−7.60 vs a caller**, free vs a reg. `sticky` (the caller) is **−1.87 vs a
   value-bettor but +2.12 vs a bluffer** (it catches the bluffs). over_bluff and sticky are **duals
   of the same caller-vs-bluffer matchup** — measured from both sides. This is the rock-paper-scissors
   texture: a leak is a *weakness* vs one attacker and a *defense* vs another. **The skill gradient
   isn't a scalar — it's a matchup graph.**

**Implication for "make them exploitable":** a leak's exploitability lives in the *matchup*, and only
the river/commit leaks have a *fixed* counter that bites. Early-street leaks need either (a) a
**surgical/adaptive** attacker (a detector that targets the exact spot — `exploitation.py` +
`OpponentModelManager`, the parked work this revives) AND/OR (b) the leak made deeper than the floor
allows (next section). A blind clamp change on either side is insufficient; the deviation must be
spot-targeted.

### Floor-defeating leaks — INVESTIGATED (2026-05-29), see `docs/plans/FLOOR_DEFEATING_LEAKS.md`

A delegated agent mapped the postflop pipeline and confirmed the structural cap: the **`defense_floor`
runs AFTER the leak layer** (step 6.b → defense_floor) and *only ever raises `call`*, structurally
re-adding any over-folded continue for made hands (air/bluff_catcher are no-floor rows — why the
narrow over-fold was free even without it). `math_floor` is a secondary hard veto (short-stack /
pot-committed). The **river bluff guardrail runs BEFORE** the leak layer, which is why `over_bluff`
survives uncapped — the asymmetry is pure layer order.

- **POC (monkeypatched both floors in-process, fit_or_fold, 6k/cell):** neutering the floors shifts
  cost in the −EV direction in **every** roster (jeff +0.93, punisher +0.88, **maniac +6.22** toward
  −EV — largest where the leak fires most), the predicted signature — but CIs straddle 0 at 6k.
  **Second co-limiter found: the over-fold barely fires a divergent action (0.3–3.5%)**, and neutering
  the floor *raised* divergence (direct evidence the floor was silently undoing fold mass).
- **Minimal safe design:** a per-decision **`floor_exempt`** flag (defaults False, set only when a
  floor-defeating tendency fires, threaded into `apply_defense_floor` like the `disable_rules` hook).
  Decision-scoped, OFF = byte-identical, the leak's `max_per_action_shift` still bounds the bleed,
  scope to the *defense* floor only (defeating the math veto would fold pot-committed hands = a bug).
- **Go/No-Go: conditional GO, sequenced** — FIRST raise fire-rate / widen the gate (the exemption
  can't matter at 0.3–3.5% divergence); if a leak can't fire materially more, No-Go and switch to a
  higher-fire-rate turn-commit leak. THEN build the flag (~½ day, ~30–50 LOC + tests) and re-price at
  a CI-clearing sample. **This `floor_exempt` flag IS the concrete form of the dynamic-clamp idea:**
  "relax the EV-bound for this decision because something (a fired detector, or a configured leak)
  justifies it." Same mechanism serves the attacker side (exploit harder on a confident read) and the
  leak side (a deliberately deeper weakness).

### Attacker side — static loop (sticky ↔ value-overbet), measured 2026-05-29

First half of the attacker program ("matrix first, then one attacker" → static loop first). Added
`--opp-spot-tendency` (put the leak on the opponent) + a balanced `tag` opponent roster, and A/B'd
the **built value-overbet** (hero layer, `--a base --b base --overbet-b`, so the only hero
difference is the overbet) against a **sticky opponent** vs a plain one, 24k HU:

| Attacker (overbet) vs … | extraction (bb/100) | CI |
|---|---|---|
| **sticky-TAG opponent** | **+16.46** | [+6.02, +26.91] — CI-clear |
| plain-TAG opponent (control) | +10.81 | [+0.56, +21.06] — CI-clear |
| **selective (sticky − plain)** | **+5.65** | overlapping CIs — directional, NOT resolved at 24k |

**Read:** the loop **closes** — the built overbet extracts CI-clear +16.46 from the constructed
sticky leak. But the honest nuance: the overbet is a **broad** exploit (+10.81 even vs a balanced
TAG, because TAG pays overbets — it isn't a sizing-reader), so its edge is **not specific** to the
sticky leak; the leak-attributable margin (~+5.6) is directional, not CI-clear. This is the matrix
lesson restated: a *clean leak-specific* attacker must be **surgical** — i.e. the **adaptive
detector** that enables the overbet **only** on a detected-sticky opponent (and no-ops vs balanced).
That's the deferred part 3; the dynamic/`floor_exempt`-style clamp keyed on detection confidence is
its core. To tighten the *static* attribution without the detector, add an opponent-side A/B
(`--opp-disable`, not yet built) so the leak can be toggled per arm under CRN.

**Status:** static loop demonstrated (attacker profits vs the leak). Adaptive detector + clean
leak-attribution = next, when picked up.

### Adaptive detector (adaptive overbet) — BUILT, MEASURED, two findings (2026-05-29)

Built the surgical attacker: `adaptive_overbet` flag (default OFF, byte-identical) →
`_effective_overbet_fraction()` gates the overbet on the live `value_vs_station` detection
(`_last_value_vs_station_intensity_raw`, set by `_apply_exploitation`); fires only on a detected
payer. Per-personality via a `"adaptive_overbet": true` key (read in `__init__`). 6 unit tests on
the gate logic. Measured via `exploit_bb100 --change adaptive_overbet` (CRN, adaptive ON vs static
OFF, shared opponent model). **Two findings, both negative — keep the build, but it is NOT a
current win:**

1. **It doesn't fire in the multiway harness — root-caused.** vs a CallStation+FoldyBot backdrop
   the adaptive arm makes **+347 bb/100 vs the static arm's +667 → −320 CI-clear** (and the
   threshold-gate variant was *byte-identical*, proving both collapse to ~0 overbet). Cause: the
   gate reads `compute_value_vs_station_intensity`, which applies a **safety dampener**
   (`safety = 1 − weight·tightness`) keyed on tight players in the field. The two **FoldyBots**
   (maximally tight) drive tightness→1 → safety→0 → **intensity→0**, so the overbet never fires.
   That dampener is correct for *thin* value-betting (a nit may have you beat) but **wrong for a
   nutted overbet** (you don't fear the nit). **The bug is the signal, not the gate shape.**
   **Real fix:** gate the overbet on **raw payer-presence** (the un-dampened station `upside`, or a
   "≥1 confidently-detected hyper-passive station in the continuing field" boolean), not the
   thin-value intensity — combined with a **threshold** (fire full once detected, don't
   linear-scale). Needs exposing that un-dampened signal from `_apply_exploitation`.
2. **Even fixed, it has no target in the current field — the deeper finding.** Every available
   backdrop **pays** the overbet: static makes **+667 vs CallStation, +373 vs GTO-Lite/ABCBot**
   ("competent" rule bots are not sizing-readers — they call overbets too). The adaptive overbet's
   only edge is dodging the static's **−24 vs a sizing-reader** (D1 oracle, `ab_node_attribution`),
   and **no sizing-reader exists in the live field**. So a perfectly-calibrated adaptive overbet
   converges to **≈ static** here — selectivity can only *match* (best case) or *lose* (mis-gated).
   Its value is **prospective**: it materializes when sizing-reading opponents exist (the revived
   sizing-aware program, or a future skilled bot). Same homogeneous-field lesson that parked the
   original sizing-aware work.

**Disposition:** kept (default OFF, byte-identical, unit-tested; the mechanism + the dynamic-clamp
pattern are the deliverable). **Sherlock Holmes' wired `adaptive_overbet:true` is currently inert**
(the signal reads ~0 → he plays as a normal TAG, no overbet) — harmless, not negative, pending the
signal fix. The static overbet remains the right default for the current payer-heavy field. Do NOT
ship adaptive-as-default until (a) the un-dampened payer signal is wired AND (b) the field actually
contains sizing-readers to make selectivity pay.

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
| over-fold to 2nd barrel | turn facing bet, marginal made | double-barrel | **priced (unmeasurable in HU — spot too rare; ~free)** | partial (multistreet H2, off) |
| fit-or-fold / over-fold to c-bet | flop facing c-bet, non-strong (widened to 2nd pair+draws) | barrel relentlessly | **priced (free, STYLE — widening didn't make it −EV; floor-capped)** | partial (`exploitation.py`) |
| auto-c-bet (c-bets 100% w/ initiative) | flop, initiative, unopened | float / raise their c-bets | **priced (free*, see below)** | — |
| under-bluff river (no triple barrel) | river, air, as bettor | over-fold their river bets; call their turn bets | **priced (free/+EV, style/face-up)** | — |
| sticky / pays off (can't fold) | facing river bet/raise, weak made | value-bet thin + overbet, never bluff | **priced (−1.87 vs value, +2.12 vs bluffer — matchup-signed)** | **built✅** (overbet, +42 vs payers) |
| over-bluff (too many bluffs) | river, air, as bettor | over-call bluff-catchers | **priced (−7.60 vs a pure caller — CI-clear exploitable)** | **built✅ as defense** (river guardrail) |
| face-up sizing (big=strong, min-raise=nuts, overbet=nuts) | any bet node; strength→size | read size → call/fold | backlog (**strategic**) | parked (sizing-aware D1) |
| over-fold to 3-bet | preflop facing 3-bet | 3-bet wide as a bluff | backlog | — |
| face-up / nitty 3-bet (value only) | preflop 3-bet decision | fold to their 3-bets, stop paying | backlog | — |
| open-limp | preflop RFI | iso-raise wide | backlog | — |
| donk-when-weak / tiny donk | OOP lead, weak | raise it | **priced (free, STYLE/face-up)** | — |
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
