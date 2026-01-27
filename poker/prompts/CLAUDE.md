# Prompt Injection System

This document explains the conditional prompt sections injected into AI player decisions based on game state.

## Overview

The `decision.yaml` template contains sections that are conditionally included based on detected game situations. This helps the AI make better decisions in specific scenarios where it historically struggled.

## Conditional Sections

### `pot_committed`

**Trigger:** When `already_bet_bb > stack_bb` (invested more BB than remaining) OR when `pot_odds >= 20` and `cost_to_call_bb < 5` (extreme odds with small call).

**Problem it solves:** AI players were folding after investing significant chips, even with extreme pot odds (sometimes 200:1+). Analysis found 670 cases losing $1.4M in expected value.

**Example scenario:**
- Player invested 36 BB, has 2 BB remaining
- Pot odds: 50:1 (needs only 2% equity to call profitably)
- AI was folding, forfeiting 36 BB to "save" 2 BB

**The injection:**
```
⚠️ POT COMMITTED: You've invested {already_bet_bb} BB with only {stack_bb} BB left.
At {pot_odds}:1 odds, you only need {required_equity}% equity to call.
Folding forfeits your {already_bet_bb} BB to save {cost_to_call_bb} BB - usually wrong.
```

**Testing:** Validated using `experiments/replay_with_guidance.py` on captured prompts. 5/5 test cases changed from incorrect fold to correct call.

**Edge cases:** ~6% of triggered cases are situations where the player is drawing dead on the river (<3% equity). The guidance may cause incorrect calls in these rare cases, but the net benefit is positive (636 helped vs 40 hurt).

---

### `short_stack`

**Trigger:** When `stack_bb < 3` (less than 3 big blinds remaining).

**Problem it solves:** AI players with tiny stacks were folding instead of shoving, even though blinds would eliminate them anyway. Push/fold strategy is mathematically required with short stacks.

**Example scenario:**
- Player has 1.5 BB remaining
- AI folds "weak" hands like Q7s pre-flop
- Should shove any playable hand (pairs, aces, broadway, suited connectors)

**The injection:**
```
⚠️ SHORT STACK ALERT:
You have less than 3 big blinds ({stack_bb} BB).
- With a short stack, you MUST play push/fold poker
- ANY playable hand = ALL-IN
- Folding means blinds will eliminate you anyway
```

---

### `made_hand` (strong/moderate × firm/soft)

**Trigger:** Post-flop when equity >= 65% (moderate tier) or >= 80% (strong tier).

**Problem it solves:** AI players were folding strong hands due to emotional state (tilt) or misreading hand strength. Batch testing showed 70% improvement on folded strong hands.

**Tiers:**
- **Strong (80%+ equity):** "Folding is almost never correct" - very direct guidance
- **Moderate (65-79% equity):** "decent showdown value" - softer guidance

**Tone adaptation:**
- **Firm:** For clear-headed players (valence >= -0.2)
- **Soft:** For tilted players (valence < -0.2) - easier to override for personality expression

**Example injection (strong_firm):**
```
🃏 STRONG HAND: You have {hand_name} (~{equity}% equity).
Folding is almost never correct here. Extract value or protect your hand.
```

---

## Toggle: `situational_guidance`

All conditional sections (pot_committed, short_stack, made_hand) can be disabled via the `situational_guidance` toggle in PromptConfig:

```python
# Disable all situational guidance
prompt_config = PromptConfig(situational_guidance=False)
```

Or in experiment config JSON:
```json
{
  "prompt_config": {
    "situational_guidance": false
  }
}
```

When disabled, the AI receives no coaching prompts for any of these scenarios.

---

## Design Principles

1. **BB normalization:** All values expressed in big blinds to help AI reason about relative sizes consistently across different stake levels.

2. **Concise guidance:** Keep injections short. Long explanations get ignored or confuse the model.

3. **Tested with replay:** Use `experiments/replay_with_guidance.py` to validate guidance on captured decision scenarios before integrating.

4. **Accept imperfection:** Edge cases will exist. Optimize for net positive impact, not 100% accuracy.

## Files

- `poker/prompts/decision.yaml` - Template sections
- `poker/prompt_manager.py` - `render_decision_prompt()` assembles sections
- `poker/controllers.py` - `_get_ai_decision()` calculates trigger conditions
- `experiments/replay_with_guidance.py` - Testing tool with predefined guidance variants

## Maintaining This Document

- **Document all changes:** When adding or modifying prompt injections, add an entry explaining the reasoning, what problem it solves, and how it was tested.
- **Clean up removals:** If a prompt section is removed or deprecated, remove its documentation from this file. Don't leave stale entries.
- **Include test results:** Note how guidance was validated (replay tests, A/B experiments, etc.) and any known edge cases.

## Analysis Source

See `docs/analysis/AI_DECISION_QUALITY_REPORT.md` for the full analysis that identified these issues.

---

## Personality and Response Format Toggles

The prompt system supports toggleable personality and response format through `PromptConfig` flags. This provides baseline testing and A/B comparison without separate code paths.

### Toggles

| Toggle | Default | Effect |
|--------|---------|--------|
| `include_personality` | `true` | When `false`, replaces personality system prompt with generic poker player prompt |
| `use_simple_response_format` | `true` | When `true`, uses simple `{"action": "...", "raise_to": ...}` JSON format instead of rich response with dramatic_sequence |

### Usage

```python
# Full baseline (no personality, simple format)
config = PromptConfig(include_personality=False, use_simple_response_format=True)

# Personality with simple format (useful for A/B testing response complexity)
config = PromptConfig(include_personality=True, use_simple_response_format=True)

# Full personality with rich format (default)
config = PromptConfig()
```

### How It Works

All prompt modes use the **same unified code path** in `decide_action()`:
- `build_base_game_state()` always produces BB-normalized game state
- `_build_decision_prompt()` conditionally includes pot odds, coaching, GTO via YAML templates
- `_get_ai_decision()` swaps system prompt if `include_personality=False` (restored via try/finally)
- `_normalize_response()` sets defaults for missing rich fields when `use_simple_response_format=True`

### Experiment Configs

Pre-built configs for testing:
- `experiments/configs/minimal_prompt_test.json` - Quick baseline test
- `experiments/configs/prompt_ablation_study.json` - Compare minimal vs full
- `experiments/configs/minimal_model_comparison.json` - Compare models

### Files

- `poker/controllers.py` - `build_base_game_state()`, personality toggle in `_get_ai_decision()`
- `poker/prompt_config.py` - `include_personality`, `use_simple_response_format` toggles
- `poker/minimal_prompt.py` - Utility functions (position mapping, BB conversion)
