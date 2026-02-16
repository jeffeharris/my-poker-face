---
purpose: Handoff notes for Phase 1 Preflop Core implementation - current state and remaining work
type: guide
created: 2026-02-16
last_updated: 2026-02-16
---

# Phase 1: Preflop Core — Handoff Plan

## Current State: ~85% Complete

All code is written, all tests pass (123 tests), but validation shows insufficient archetype stat separation. The personality modifier works correctly — the issue is in the interaction between chart data and deviation profile scales.

## What's Done

### New Files Created
```
poker/strategy/
  __init__.py                    # Public API with try/except lazy imports
  nodes.py                       # PreflopNode, PostflopNode (frozen dataclasses)
  strategy_profile.py            # StrategyProfile with sample_action()
  strategy_table.py              # StrategyTable loader, lookup, fallbacks
  preflop_classifier.py          # Game state → PreflopNode mapping
  personality_modifier.py        # Logit-space distortion: modify_strategy(), clamp_divergence()
  deviation_profiles.py          # DeviationProfile dataclass + 6 predefined profiles
  action_mapper.py               # Abstract action → concrete game engine action + sizing
  data/
    preflop_100bb_6max.json      # Preflop charts (169 hands × 50 scenarios)

poker/tiered_bot_controller.py   # TieredBotController (subclass of AIPlayerController)

tests/test_strategy/
  __init__.py
  test_nodes.py                  # 9 tests
  test_strategy_profile.py       # 5 tests
  test_strategy_table.py         # 20 tests
  test_preflop_classifier.py     # Tests for classifier
  test_personality_modifier.py   # Tests for modifier
  test_action_mapper.py          # 13 tests
  test_tiered_bot_controller.py  # 9 tests

experiments/validate_preflop.py  # Bot-vs-bot validation script
```

### Modified Files
- `flask_app/handlers/game_handler.py` — added `'tiered'` branch to `restore_ai_controllers()`

### All Tests Pass
```bash
python3 scripts/test.py test_strategy    # 114 passed
python3 scripts/test.py test_tiered_bot  # 9 passed
```

## What Remains: Validation Tuning

### Problem
The validation script shows **insufficient archetype stat separation**:
- VPIP ranges from 27% (TAG) to 30% (Calling Station) — directionally correct but too narrow
- PFR is identical across all archetypes (20.3%)
- The LAG > TAG > Rock VPIP ordering isn't achieved

### Root Cause Analysis
1. **PFR flatness**: In RFI scenarios (70% of simulated hands), the only voluntary action is "raise". The personality modifier shifts raise vs fold probabilities, but since any raise = both VPIP and PFR, these stats are coupled. PFR separation requires vs_open scenarios (where call = VPIP but not PFR).

2. **Narrow VPIP spread**: The modifier's KL clamping + per-action caps limit how far probabilities can shift. With `max_per_action_shift` of 0.10-0.30 and `max_kl` of 0.2-0.6, the absolute effect on the sampled action is small.

3. **Chart structure**: The charts use 85/15 mixed frequencies for in-range/out-of-range hands. This gives the modifier room to work but the deviation budget may be too tight.

### Recommended Next Steps (Priority Order)

#### 1. Tune deviation profile scales (30 min)
Increase `aggression_scale` and `looseness_scale` in `DEVIATION_PROFILES`:
- Try 2-3x the current values
- Run validation after each change
- The KL clamp will still bound total divergence, so this is safe

#### 2. Adjust validation scenario mix (15 min)
The current validation simulates 70% RFI / 20% vs_open / 10% vs_3bet. Real poker has more varied scenarios. Adjust to 50% RFI / 35% vs_open / 15% vs_3bet to better test separation.

#### 3. Consider the risk_scale multiplier on fold actions (15 min)
In `compute_trait_offsets`, the risk_identity only affects `jam` and `passive`. Adding a fold penalty scaled by looseness would increase VPIP separation for loose archetypes.

#### 4. Widen chart mixed frequencies for boundary hands (30 min)
Instead of uniform 85/15 splits, use a gradient:
- Premium hands: 95/5 (almost always raise)
- Mid-range hands: 70/30 (more room for personality)
- Trash hands: 10/90 (almost always fold)
This gives more distortion surface area for the modifier.

## Key Architecture Decisions Made
- Strategy tables use mixed frequencies (not pure) to enable personality distortion
- PostflopNode is a v2-ready stub; Phase 1 uses check/fold fallback
- `__init__.py` uses try/except imports for incremental module availability
- Generator script at `/tmp/generate_preflop_charts.py` for regenerating charts

## How to Test
```bash
# Unit tests
python3 scripts/test.py test_strategy
python3 scripts/test.py test_tiered_bot

# Validation (runs inside Docker)
docker compose exec backend python -m experiments.validate_preflop --hands 10000

# Manual smoke test
# Start game with bot_types={'Batman': 'tiered'}, check logs for [TIERED_BOT] output
```
