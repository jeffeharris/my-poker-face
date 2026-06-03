---
purpose: Handoff notes for Phase 1 Preflop Core implementation
type: guide
created: 2026-02-16
last_updated: 2026-02-16
---

> **ARCHIVED 2026-06-03** — historical artifact, no longer maintained. Kept for the record; do not treat as current. See `docs/technical/TODO.md`.

# Phase 1: Preflop Core — Complete

## Status: DONE

All code written, 123 unit tests passing, validation passing across seeds with meaningful archetype separation.

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

## Validation Results (10k hands, stable across seeds)

```
Archetype               VPIP%     PFR%   3-bet%
Nit                     22%      12%      9%
Rock                    22%      12%     10%
TAG                     25%      18%     17%
Calling Station         35%      20%     17%
LAG                     35%      27%     28%
Maniac                  48%      41%     46%
```

All directional checks pass: LAG > TAG > Rock for VPIP and PFR, Maniac > LAG, Nit < Rock. No PFR > VPIP violations. All VPIP in [5%, 85%].

### Tuning applied
1. Deviation profile scales doubled (~2x aggression, looseness, risk, KL caps)
2. Graduated chart frequencies (premium 95% → trash 2%) replacing flat 85/15 splits
3. Scenario mix adjusted to 50% RFI / 35% vs_open / 15% vs_3bet
4. Fixed `_is_action_legal` bug in personality modifier (abstract action mapping)

### Known limitations
- Nit/Rock VPIP (~22%) is higher than real poker nits (~10-15%). The KL cap limits how far the modifier can push toward fold. Acceptable for Phase 1.
- Postflop is check/fold fallback (Phase 2 scope).

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
