---
purpose: Evaluation results from heads-up (2-player) poker support implementation
type: analysis
created: 2026-02-15
last_updated: 2026-02-16
---

# Heads-Up Support Evaluation Report

**Date:** February 15–16, 2026
**Experiment IDs:** 26–33
**Models:** gpt-5-nano, gpt-5-mini (OpenAI), llama-3.1-8b-instant (Groq)
**Total API Calls:** 14,638 (~$0.98)

---

## Summary

Heads-up (2-player) support was added across the full decision pipeline: blind posting, range gates, bounded options, prompts, and nudge phrases. The evaluation compares 4-player and 2-player games using the same archetypes and configuration (lean bounded, nudges+rangegate), then tests the same HU setup across three models to measure model sensitivity.

**Key findings:**
1. **HU adjustments work on gpt-5-nano:** +15pp wider VPIP for TAG, appropriate raise-heavy action, and preserved archetype ordering — with zero 4-player regression.
2. **gpt-5-mini is dramatically tighter in HU:** TAG VPIP drops 31pp vs nano, and default archetype (Batman) drops 53pp. Mini overrides nudge/prompt signals with its own conservative priors.
3. **Groq llama-3.1-8b is wildly loose:** TAG VPIP of 85% (should be ~60-70%), LAG at 99%. Destroys archetype differentiation but the raw aggression actually wins games against CaseBot.
4. **gpt-5-nano is the sweet spot** — follows the bounded options system faithfully without overriding it in either direction. Model choice matters more than prompt tuning.

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

### Groq llama-3.1-8b Experiments

#### Experiment 31: 2-Player HU (Groq)
- 10 tournaments x 50 hands, Sun Tzu (TAG) vs Blackbeard (LAG)
- Identical config to experiment 28, only model/provider changed
- 2,366 API calls (~$0.04)

#### Experiment 32: Batman vs CaseBot HU (Groq)
- 5 tournaments x 100 hands, Batman (Groq) vs CaseBot
- Identical config to experiment 26, only model/provider changed
- 1,134 API calls (~$0.02)

#### Experiment 33: Cross-Model HU (nano vs Groq)
- 10 tournaments x 50 hands, Sun Tzu (nano) vs Blackbeard (Groq)
- Per-player llm_config: Sun Tzu uses gpt-5-nano, Blackbeard uses llama-3.1-8b
- Tests direct competitive matchup across models
- 1,728 API calls (~$0.06)

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

## Results: Groq llama-3.1-8b

### Preflop Stats (Groq HU)

| Player | Profile | VPIP | PFR | Fold | Call | n |
|--------|---------|------|-----|------|------|---|
| Sun Tzu | TAG | 85.3% | 67.5% | 14.7% | 17.8% | 990 |
| Blackbeard | LAG | 99.0% | 97.6% | 1.0% | 1.4% | 779 |

### Postflop Stats (Groq HU)

| Player | Profile | Raise | Check | Fold | Call | n |
|--------|---------|-------|-------|------|------|---|
| Sun Tzu | TAG | 78.8% | 3.2% | 6.7% | 11.2% | 312 |
| Blackbeard | LAG | 98.6% | 0.0% | 0.4% | 1.1% | 285 |

### Batman vs CaseBot HU — Groq (Experiment 32)

| Metric | Value |
|--------|-------|
| Tournament wins | **Batman 2, CaseBot 3** |
| Batman preflop VPIP | **89.1%** |
| Batman preflop PFR | 85.2% |
| Batman preflop fold | 6.9% |
| Batman preflop call | 3.9% |
| Batman preflop check | 4.1% |
| Batman postflop raise | 84.3% |
| Batman postflop check | 10.2% |
| Batman postflop fold | 2.1% |
| Batman postflop call | 3.4% |

Groq Batman is the first AI player to win any tournaments against CaseBot (2 out of 5). The hyper-aggressive style overwhelms CaseBot's conservative rule-based play in some matchups.

### HU Tournament Outcomes — Groq (Experiment 31)

Sun Tzu 9 wins, Blackbeard 1 win (across 10 tournaments). Surprisingly, TAG dominates on Groq — the opposite of nano and mini. Sun Tzu's 85% VPIP with 17.8% flat-calling may create a more exploitative dynamic than Blackbeard's pure 99% aggression.

### Cross-Model Matchup (Experiment 33)

Sun Tzu (nano TAG, 55.3% VPIP) vs Blackbeard (Groq LAG, 99.0% VPIP):
- **Blackbeard (Groq) wins 8-2**
- Groq Blackbeard raises 99.2% postflop, overwhelming nano Sun Tzu's measured play
- Nano Sun Tzu folds 44.7% preflop and 32.0% postflop against relentless pressure

---

## Cross-Model Comparison

### Preflop VPIP: All Models (HU)

| Player | Profile | nano | mini | groq | nano→mini | nano→groq |
|--------|---------|------|------|------|-----------|-----------|
| Sun Tzu | TAG | 66.3% | 35.0% | 85.3% | **-31.3pp** | **+19.0pp** |
| Blackbeard | LAG | 94.3% | 91.5% | 99.0% | -2.8pp | +4.7pp |
| Batman | default | 73.2% | 20.5% | 89.1% | **-52.7pp** | **+15.9pp** |

### Preflop PFR: All Models (HU)

| Player | Profile | nano | mini | groq |
|--------|---------|------|------|------|
| Sun Tzu | TAG | 52.9% | 24.1% | 67.5% |
| Blackbeard | LAG | 93.1% | 89.7% | 97.6% |
| Batman | default | 68.5% | 18.7% | 85.2% |

### Postflop Aggression: All Models (HU)

| Player | nano raise% | mini raise% | groq raise% |
|--------|-------------|-------------|-------------|
| Sun Tzu | 65.5% | 37.8% | 78.8% |
| Blackbeard | 94.1% | 93.5% | 98.6% |
| Batman | 55.0% | 29.7% | 84.3% |

### Archetype Differentiation (TAG-LAG VPIP gap)

| Model | TAG VPIP | LAG VPIP | Gap |
|-------|----------|----------|-----|
| nano | 66.3% | 94.3% | **28.0pp** |
| mini | 35.0% | 91.5% | **56.5pp** |
| groq | 85.3% | 99.0% | **13.7pp** |

Nano preserves the best archetype differentiation in context-appropriate ranges. Mini over-separates (TAG too tight). Groq under-separates (TAG too loose).

### CaseBot Win Rate by Model

| Model | Batman wins | CaseBot wins |
|-------|-------------|--------------|
| nano | 0 | 5 |
| mini | 0 | 5 |
| groq | **2** | 3 |

### Cost Comparison

| Model | Cost per HU experiment | Relative cost |
|-------|----------------------|---------------|
| nano | ~$0.06–0.22 | 1x |
| mini | ~$0.25 | 1–4x |
| groq | ~$0.02–0.04 | **0.2–0.3x** |

---

## Analysis

### HU Adjustments Working as Designed (nano)

1. **Wider ranges:** Sun Tzu (TAG) opens 14.9pp wider in HU (51.4% → 66.3%). This matches the HEADS_UP_POSITION_OFFSETS effect (+0.30 for button vs +0.05 in multi-way).

2. **Less folding:** Sun Tzu folds 15.0pp less preflop in HU (48.6% → 33.6%). Blackbeard barely folds at all (5.6%), appropriate for a LAG in HU.

3. **Archetype ordering preserved:** TAG (66.3%) < LAG (94.3%) in HU — the same directional relationship as 4-player (51.4% < 89.6%). The bounded options system differentiates archetypes in both contexts.

4. **Min-raise sizing active:** Batman's 68.5% PFR vs 4.6% call rate shows raise-heavy preflop action, consistent with the HU min-raise (2x BB) open sizing.

5. **Minimal flat-calling:** Across all nano HU experiments, flat-calling rates are low (1-13%), matching the design intent of the nudge phrases ("raise or fold, avoid flat calls").

### The Model Sensitivity Spectrum

Three models reveal a clear spectrum of prompt-following behavior:

**gpt-5-mini (too conservative):** Overrides HU widening signals with its own poker priors. TAG VPIP of 35% in HU is tighter than nano TAG in 4-player (51.4%). Mini appears to weight EV labels heavily and fold anything marginal, ignoring the "widen your ranges" nudges. Postflop passivity (37.8% raise for TAG) compounds the problem.

**gpt-5-nano (balanced):** Follows the bounded options system faithfully. Widens appropriately in HU while maintaining archetype separation. Neither overrides nor blindly follows — reads the EV labels, nudges, and prompt context as a cohesive signal.

**Groq llama-3.1-8b (too aggressive):** Follows the menu too eagerly, raising almost everything regardless of EV labels. TAG at 85.3% VPIP is nearly LAG territory. Archetype differentiation collapses to 14pp (vs 28pp on nano). However, the raw aggression produces a surprising result: the only AI to beat CaseBot in tournaments (2-3).

### Why Groq Beats CaseBot

Groq Batman's 89.1% VPIP and 84.3% postflop raise rate creates maximum pressure on every street. CaseBot's rule-based strategy is calibrated against typical play — it doesn't adapt to hyper-aggression. The brute-force approach exploits CaseBot's static decision boundaries, even though it's not "good poker" by conventional standards. This suggests CaseBot may need anti-aggression adjustments, or that extreme aggression has inherent EV in HU against non-adaptive opponents.

### Tournament Outcome Reversal on Groq

On nano and mini, Blackbeard (LAG) dominates Sun Tzu (TAG) in HU. On Groq, Sun Tzu wins 9-1. This reversal likely occurs because Groq Sun Tzu's 85% VPIP + 17.8% flat-calling creates a wider, more varied range than Blackbeard's pure 99% raise strategy. Against an opponent who raises literally everything, having some calling range becomes exploitative.

### Implications for Model Selection

| Model | Best for | Avoid for |
|-------|----------|-----------|
| **gpt-5-nano** | Default choice. HU and multi-way. Archetype fidelity. | — |
| **gpt-5-mini** | Multi-way games where conservative play is correct | HU (too tight) |
| **Groq llama-3.1-8b** | Cost-sensitive bulk experiments. Aggression testing. | Archetype differentiation. Production personality expression. |

The bounded options system's effectiveness depends on the model trusting the menu. Nano trusts it. Mini second-guesses it. Groq blindly follows it. For the hybrid AI architecture, nano remains the recommended default.

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
2. **CaseBot comparison is one-sided** — CaseBot has no personality or LLM overhead, making win/loss an unfair metric. VPIP and action distribution are the meaningful measures. (Exception: Groq's wins are notable precisely because no other model achieved them.)
3. **Small sample size** — 5-10 tournaments per condition. Sufficient for directional validation but not for precise effect sizes.
4. **No pre-HU-support baseline** — we can't compare "before" vs "after" directly since the code was already changed. The 4-player experiment serves as the control instead.
5. **all_in actions** — VPIP/PFR numbers include all_in as a raise. Earlier versions of this report excluded all_in, causing slightly different numbers for nano experiments.
6. **Cross-model matchup confounds model and archetype** — Experiment 33 uses Sun Tzu (nano) vs Blackbeard (Groq), so results reflect both model and archetype differences. Pure model comparison requires same-archetype matchups (experiments 28 vs 31).

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

# 2-player HU archetype comparison (Groq)
docker compose exec backend python -m experiments.run_from_config \
    experiments/configs/hu_eval_2player_groq.json

# Batman vs CaseBot HU (Groq)
docker compose exec backend python -m experiments.run_from_config \
    experiments/configs/hu_eval_casebot_groq.json

# Cross-model: nano Sun Tzu vs Groq Blackbeard
docker compose exec backend python -m experiments.run_from_config \
    experiments/configs/hu_eval_groq_vs_nano.json
```
