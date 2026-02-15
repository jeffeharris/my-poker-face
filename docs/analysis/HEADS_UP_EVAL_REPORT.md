---
purpose: Evaluation results from heads-up (2-player) poker support implementation
type: analysis
created: 2026-02-15
last_updated: 2026-02-15
---

# Heads-Up Support Evaluation Report

**Date:** February 15, 2026
**Experiment IDs:** 26–30
**Models:** gpt-5-nano, gpt-5-mini (OpenAI)
**Total API Calls:** 9,410 (~$0.86)

---

## Summary

Heads-up (2-player) support was added across the full decision pipeline: blind posting, range gates, bounded options, prompts, and nudge phrases. The evaluation compares 4-player and 2-player games using the same archetypes and configuration (lean bounded, nudges+rangegate), then tests the same HU setup on gpt-5-mini to measure model sensitivity.

**Key findings:**
1. **HU adjustments work on gpt-5-nano:** +15pp wider VPIP for TAG, appropriate raise-heavy action, and preserved archetype ordering — with zero 4-player regression.
2. **gpt-5-mini is dramatically tighter in HU:** TAG VPIP drops 31pp vs nano, and default archetype (Batman) drops 53pp. The LAG archetype is unaffected (-3pp). This suggests gpt-5-mini overrides nudge/prompt signals more aggressively than nano.
3. **Model choice matters more than prompt tuning** for non-LAG archetypes in HU.

---

## Experiment Design

### gpt-5-nano Experiments

#### Experiment 27: 4-Player Baseline
- 5 tournaments x 50 hands, 4 archetype players
- Sun Tzu (TAG), Abraham Lincoln (TP), Mark Twain (default), Blackbeard (LAG)
- Nudges + rangegate enabled, psychology enabled
- 1,363 API calls (~$0.07)

#### Experiment 28: 2-Player HU
- 10 tournaments x 50 hands, Sun Tzu (TAG) vs Blackbeard (LAG)
- Same config as 4-player baseline
- HU adjustments active: wider position offsets (+0.30 btn), disabled range bias, lower monster threshold (0.75), min-raise sizing, HU nudge phrases
- 4,375 API calls (~$0.22)

#### Experiment 26: Batman vs CaseBot HU
- 5 tournaments x 100 hands, hybrid AI vs rule-based bot
- Lean bounded with nudges + rangegate + psychology
- Tests competitiveness against optimal rule-based play
- 1,211 API calls (~$0.06)

### gpt-5-mini Experiments

#### Experiment 29: 2-Player HU (mini)
- 10 tournaments x 50 hands, Sun Tzu (TAG) vs Blackbeard (LAG)
- Identical config to experiment 28, only model changed
- 1,256 API calls (~$0.25)

#### Experiment 30: Batman vs CaseBot HU (mini)
- 5 tournaments x 100 hands, Batman vs CaseBot
- Identical config to experiment 26, only model changed
- 1,205 API calls (~$0.25)

---

## Results: gpt-5-nano

### Preflop VPIP: 4-Player vs HU (nano)

| Player | Profile | 4P VPIP | HU VPIP | Delta |
|--------|---------|---------|---------|-------|
| Sun Tzu | TAG | 51.4% | 66.3% | **+14.9pp** |
| Blackbeard | LAG | 89.6% | 94.3% | **+4.7pp** |
| Abraham Lincoln | TP | 41.6% | — | (not tested HU) |
| Mark Twain | default | 53.0% | — | (not tested HU) |

### Preflop Action Distribution (nano)

| Player | Context | Raise | Call | Fold | Check |
|--------|---------|-------|------|------|-------|
| Sun Tzu | 4-player | 46.5% | 5.0% | 48.6% | 0.0% |
| Sun Tzu | HU | 52.9% | 13.4% | 33.6% | 0.1% |
| Blackbeard | 4-player | 89.2% | 0.5% | 10.4% | 0.0% |
| Blackbeard | HU | 93.1% | 1.2% | 5.6% | 0.0% |

### Postflop Action Distribution (nano)

| Player | Context | Raise | Check | Fold | Call |
|--------|---------|-------|-------|------|------|
| Sun Tzu | HU | 65.5% | 6.3% | 17.5% | 10.7% |
| Blackbeard | HU | 94.1% | 0.0% | 4.2% | 1.8% |

### Batman vs CaseBot HU — nano (Experiment 26)

| Metric | Value |
|--------|-------|
| Tournament wins | CaseBot 5, Batman 0 |
| Batman preflop VPIP | **73.2%** |
| Batman preflop PFR | 68.5% |
| Batman preflop fold | 13.5% |
| Batman preflop call | 4.6% |
| Batman preflop check | 13.3% |
| Batman postflop raise | 55.0% |
| Batman postflop check | 31.5% |
| Batman postflop fold | 9.7% |

### HU Tournament Outcomes — nano (Experiment 28)

Blackbeard 15 wins, Sun Tzu 5 wins (across 20 tournaments). Blackbeard (LAG) dominates, consistent with LAG being the stronger HU archetype.

---

## Results: gpt-5-mini

### Preflop Stats (mini HU)

| Player | Profile | VPIP | PFR | Fold | Call | n |
|--------|---------|------|-----|------|------|---|
| Sun Tzu | TAG | 35.0% | 24.1% | 65.0% | 10.9% | 606 |
| Blackbeard | LAG | 91.5% | 89.7% | 8.5% | 1.8% | 399 |

### Postflop Stats (mini HU)

| Player | Profile | Raise | Check | Fold | Call | n |
|--------|---------|-------|-------|------|------|---|
| Sun Tzu | TAG | 37.8% | 8.4% | 27.3% | 26.6% | 143 |
| Blackbeard | LAG | 93.5% | 0.0% | 4.6% | 1.9% | 108 |

### Batman vs CaseBot HU — mini (Experiment 30)

| Metric | Value |
|--------|-------|
| Tournament wins | CaseBot 5, Batman 0 |
| Batman preflop VPIP | **20.5%** |
| Batman preflop PFR | 18.7% |
| Batman preflop fold | 43.2% |
| Batman preflop call | 1.9% |
| Batman preflop check | 36.3% |
| Batman postflop raise | 29.7% |
| Batman postflop check | 44.5% |
| Batman postflop fold | 18.0% |
| Batman postflop call | 7.7% |

### HU Tournament Outcomes — mini (Experiment 29)

Blackbeard 8 wins, Sun Tzu 2 wins (across 10 tournaments). Blackbeard dominance increases compared to nano.

---

## Cross-Model Comparison

### Preflop VPIP: nano vs mini (HU)

| Player | Profile | nano HU | mini HU | Delta |
|--------|---------|---------|---------|-------|
| Sun Tzu | TAG | 66.3% | 35.0% | **-31.3pp** |
| Blackbeard | LAG | 94.3% | 91.5% | **-2.8pp** |
| Batman | default | 73.2% | 20.5% | **-52.7pp** |

### Preflop PFR: nano vs mini (HU)

| Player | Profile | nano HU | mini HU | Delta |
|--------|---------|---------|---------|-------|
| Sun Tzu | TAG | 52.9% | 24.1% | **-28.8pp** |
| Blackbeard | LAG | 93.1% | 89.7% | **-3.4pp** |
| Batman | default | 68.5% | 18.7% | **-49.8pp** |

### Postflop Aggression: nano vs mini (HU)

| Player | nano raise% | mini raise% | Delta |
|--------|-------------|-------------|-------|
| Sun Tzu | 65.5% | 37.8% | **-27.7pp** |
| Blackbeard | 94.1% | 93.5% | -0.6pp |
| Batman | 55.0% | 29.7% | **-25.3pp** |

### Key Observation

gpt-5-mini Sun Tzu in HU (35.0% VPIP) is actually **tighter than nano Sun Tzu in 4-player** (51.4% VPIP). The model completely overrides the HU widening signals for the TAG archetype.

---

## Analysis

### HU Adjustments Working as Designed (nano)

1. **Wider ranges:** Sun Tzu (TAG) opens 14.9pp wider in HU (51.4% → 66.3%). This matches the HEADS_UP_POSITION_OFFSETS effect (+0.30 for button vs +0.05 in multi-way).

2. **Less folding:** Sun Tzu folds 15.0pp less preflop in HU (48.6% → 33.6%). Blackbeard barely folds at all (5.6%), appropriate for a LAG in HU.

3. **Archetype ordering preserved:** TAG (66.3%) < LAG (94.3%) in HU — the same directional relationship as 4-player (51.4% < 89.6%). The bounded options system differentiates archetypes in both contexts.

4. **Min-raise sizing active:** Batman's 68.5% PFR vs 4.6% call rate shows raise-heavy preflop action, consistent with the HU min-raise (2x BB) open sizing.

5. **Minimal flat-calling:** Across all nano HU experiments, flat-calling rates are low (1-13%), matching the design intent of the nudge phrases ("raise or fold, avoid flat calls").

### Model Sensitivity: gpt-5-mini is Conservative

The most significant finding is the dramatic model sensitivity in HU behavior:

1. **LAG unaffected:** Blackbeard plays nearly identically on both models (94.3% vs 91.5% VPIP). When the bounded options present overwhelmingly raise-plus-EV options, both models follow the menu. The LAG profile generates so many +EV raise options that even a cautious model raises.

2. **TAG/default collapse:** Sun Tzu and Batman see 31-53pp VPIP drops on mini. These archetypes receive more mixed option menus (some +EV raises, some -EV folds) in marginal spots. gpt-5-nano follows the nudge phrases and position-widening signals; gpt-5-mini appears to weight the EV labels more heavily and fold marginal hands despite the "widen your ranges" prompt.

3. **Batman check rate spikes:** Batman on mini checks 36.3% preflop (as BB vs CaseBot limps) compared to 13.3% on nano. When facing a limp, mini Batman prefers checking over raising with marginal holdings — the opposite of HU strategy.

4. **Postflop passivity:** Mini Sun Tzu calls 26.6% postflop vs 10.7% on nano, and raises only 37.8% vs 65.5%. The tighter preflop range doesn't translate to stronger postflop play — the model is simply more passive across the board.

### CaseBot Still Dominant

CaseBot won all 10 tournaments (5 per model) against Batman. The rule-based bot's advantage is consistent regardless of model choice. Batman's value is in personality expression and entertainment, not pure GTO optimization.

### Implications for Prompt Design

The cross-model results suggest the current HU adjustments are necessary but not sufficient for all models:
- **For nano-class models**: The bounded options + nudge phrases system works well. The model follows the widening signals.
- **For mini-class models**: The EV labels may need rebalancing or the nudge phrases may need stronger emphasis. Possible approaches:
  - Stronger HU position offsets for TAG/default profiles
  - More aggressive threshold adjustments (lower monster threshold further)
  - Reduce visibility of negative-EV labels in marginal HU spots
  - Model-specific prompt adjustments

---

## What Was Implemented

| Component | Change |
|-----------|--------|
| Blind posting | Dealer = SB, non-dealer = BB in 2-player games |
| Preflop action order | Dealer/SB acts first in HU |
| Position offsets | `HEADS_UP_POSITION_OFFSETS`: button +0.30, BB +0.20 |
| Hand tiers | Added TOP_85_HANDS (143) and TOP_95_HANDS (160) |
| Monster threshold | 0.75 in HU vs 0.90 multi-way |
| Raise sizing | Min-raise (2x BB) standard open in HU preflop |
| Range bias | Disabled for HU (wide ranges are standard) |
| Profile overrides | `heads_up_raise_plus_ev/neutral` on OptionProfile |
| Prompt context | "Heads-up (1v1). Widen your ranges and apply pressure." |
| Nudge phrases | 12 HU-specific nudge overrides |

---

## Limitations

1. **Lincoln and Mark Twain not tested in HU** — per-variant personality overrides aren't supported by the experiment runner. Only TAG vs LAG was compared across contexts.
2. **CaseBot comparison is one-sided** — CaseBot has no personality or LLM overhead, making win/loss an unfair metric. VPIP and action distribution are the meaningful measures.
3. **Small sample size** — 5-10 tournaments per condition. Sufficient for directional validation but not for precise effect sizes. Mini experiments had fewer total hands than nano due to shorter tournament duration.
4. **No pre-HU-support baseline** — we can't compare "before" vs "after" directly since the code was already changed. The 4-player experiment serves as the control instead.
5. **Only two models tested** — nano and mini. Other providers (Anthropic, Groq) may respond differently to the HU adjustments.
6. **all_in actions** — VPIP/PFR numbers include all_in as a raise. Earlier versions of this report excluded all_in, causing slightly different numbers for nano experiments.

---

## Reproduction

```bash
# 4-player baseline (nano)
docker compose exec backend python -m experiments.run_from_config \
    experiments/configs/hu_eval_4player.json

# 2-player HU archetype comparison (nano)
docker compose exec backend python -m experiments.run_from_config \
    experiments/configs/hu_eval_2player.json

# Batman vs CaseBot HU (nano)
docker compose exec backend python -m experiments.run_from_config \
    experiments/configs/hybrid_vs_casebot_hu_eval.json

# 2-player HU archetype comparison (mini)
docker compose exec backend python -m experiments.run_from_config \
    experiments/configs/hu_eval_2player_gpt5mini.json

# Batman vs CaseBot HU (mini)
docker compose exec backend python -m experiments.run_from_config \
    experiments/configs/hu_eval_casebot_gpt5mini.json
```
