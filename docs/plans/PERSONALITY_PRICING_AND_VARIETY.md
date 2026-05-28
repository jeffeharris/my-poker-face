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

**Opponents (price is opponent-dependent — measure ≥2):**
- `jeff` (realistic sticky over-folder, HU) — the "vs the recreational field" price.
- `punisher` (disciplined reg, HU) — the "vs competent play" price (the intrinsic cost).
- Optionally 6-max (production regime) and a self-play/`Baseline` field.
- Note: a personality can be **+EV vs some opponents** (a `maniac` may beat a `nit`,
  lose to a `calling_station`). Report the per-opponent vector, not one number.

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

**Setup (run after the `--a-hero/--b-hero` extension lands + control passes):**
```
# template — repeat for ARCH in {Nit, Rock, TAG, CallStation(calling_station), LAG, Maniac}
docker compose exec -T backend python -m experiments.ab_node_attribution \
    jeff 3000 42,3042,6042,9042,12042,15042,18042,21042 \
    --a base --b base --a-hero Baseline --b-hero <ARCH> --heads-up --top 12
# then repeat vs `punisher`. (Use the exact ARCHETYPES key names — verify with a
# quick `python -c "from experiments.simulate_bb100 import ARCHETYPES; print(sorted(ARCHETYPES))"`.)
```

**Pre-committed validation / what we learn:**
- Each profile gets a `{vs jeff, vs punisher}` price vector + per-node localization.
- Sanity: directions should match the archetype (e.g. `nit` folds more → loses
  pots it could win vs a folder, may be ~neutral vs a reg; `maniac` spews → large
  −EV vs a station, maybe +EV vs a nit). A direction that contradicts the archetype
  is a wiring bug.
- Flag any profile that is **broken** (< −15 or one-node-concentrated) for a
  `max_kl` re-cap or a deviation-logic fix.

### Results (fill in)

| Profile | vs jeff (HU) bb/100 | vs punisher (HU) bb/100 | Where it bleeds (top nodes) | Verdict |
|---|---|---|---|---|
| nit | _TBD_ | _TBD_ | | |
| rock | _TBD_ | _TBD_ | | |
| tag | _TBD_ | _TBD_ | | |
| calling_station | _TBD_ | _TBD_ | | |
| lag | _TBD_ | _TBD_ | | |
| maniac | _TBD_ | _TBD_ | | |

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
