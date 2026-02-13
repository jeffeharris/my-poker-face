---
purpose: Reference for all PromptConfig fields, game modes, and experiment overrides
type: reference
created: 2026-02-12
last_updated: 2026-02-13
---

# PromptConfig Reference

`PromptConfig` (`poker/prompt_config.py`) is the central control surface for AI prompt assembly. Every boolean flag toggles a section of the decision prompt; game modes and experiments work by overriding these fields.

## How It Flows

```
Game Mode / Experiment Config
        │
        ▼
   PromptConfig          ← dataclass with defaults
        │
        ├──▶ AIPlayerController.decide_action()       (poker/controllers.py)
        │       └── PromptManager.render_decision_prompt()
        │
        └──▶ HybridAIController.decide_action()       (poker/hybrid_ai_controller.py)
                └── _decide_action_lean()  (when lean_bounded=True)
```

## Field Reference

### Game State

| Field | Type | Default | Description | Consumer |
|-------|------|---------|-------------|----------|
| `pot_odds` | bool | True | Pot odds guidance and equity calculations | controllers.py → prompt_manager.py |
| `hand_strength` | bool | True | Hand strength evaluation (preflop ranking, postflop eval) | controllers.py |
| `range_guidance` | bool | True | Looseness-aware preflop range classification | controllers.py |

### Memory

| Field | Type | Default | Description | Consumer |
|-------|------|---------|-------------|----------|
| `session_memory` | bool | True | Session stats (win rate, streaks, observations) | controllers.py |
| `opponent_intel` | bool | True | Opponent tendencies and playing style summaries | controllers.py |
| `strategic_reflection` | bool | True | Include past strategic reflections in prompts | controllers.py |
| `memory_keep_exchanges` | int | 0 | Conversation exchanges to retain (0 = clear each turn) | controllers.py, hybrid_ai_controller.py |

### Psychology

| Field | Type | Default | Description | Consumer |
|-------|------|---------|-------------|----------|
| `chattiness` | bool | True | Chattiness guidance (when/how to speak, emoji usage) | controllers.py |
| `emotional_state` | bool | True | Emotional state narrative and dimensions | controllers.py |
| `tilt_effects` | bool | True | Tilt-based prompt modifications (intrusive thoughts, zone modifiers) | controllers.py |
| `expression_filtering` | bool | True | Visibility-based expression dampening (Phase 2) | controllers.py |
| `zone_benefits` | bool | True | Zone-based strategy guidance (Phase 7) | controllers.py |

### Template Instructions

| Field | Type | Default | Description | Consumer |
|-------|------|---------|-------------|----------|
| `mind_games` | bool | True | "Read opponent table talk" instruction | controllers.py |
| `dramatic_sequence` | bool | True | Character expression and table talk generation | controllers.py |
| `betting_discipline` | bool | True | BETTING DISCIPLINE block in every decision prompt | controllers.py |

### Situational Guidance

| Field | Type | Default | Description | Consumer |
|-------|------|---------|-------------|----------|
| `situational_guidance` | bool | True | Coaching for pot-committed, short-stack, made-hand situations | controllers.py |

### GTO Foundation

| Field | Type | Default | Description | Consumer |
|-------|------|---------|-------------|----------|
| `gto_equity` | bool | False | Always show equity vs required equity comparison | controllers.py |
| `gto_verdict` | bool | False | Show explicit "+EV"/"-EV" verdict (only renders when `gto_equity` is also True) | controllers.py |
| `use_enhanced_ranges` | bool | True | PFR/action-based range estimation (vs VPIP-only) | controllers.py, hand_ranges.py |

### Personality & Response Format

| Field | Type | Default | Description | Consumer |
|-------|------|---------|-------------|----------|
| `include_personality` | bool | True | Use celebrity personality prompt (False = generic prompt) | controllers.py |
| `use_simple_response_format` | bool | False | Simple `{"action", "raise_to"}` JSON instead of rich format with dramatic_sequence | controllers.py |

### Lean Bounded Mode (Hybrid Controller)

| Field | Type | Default | Description | Consumer |
|-------|------|---------|-------------|----------|
| `lean_bounded` | bool | False | Bypass full prompt pipeline; use minimal options-only prompt | hybrid_ai_controller.py |
| `style_aware_options` | bool | True | Map psychology playstyle axes to option profiles | hybrid_ai_controller.py |

### Hand Plan — Phase 0 (Hybrid Controller)

| Field | Type | Default | Description | Consumer |
|-------|------|---------|-------------|----------|
| `hand_plan` | bool | False | Generate per-hand 1-sentence strategy before decisions | hybrid_ai_controller.py |

### Option Framing (Hybrid Controller)

| Field | Type | Default | Description | Consumer |
|-------|------|---------|-------------|----------|
| `composed_nudges` | bool | False | Replace raw EV labels with playstyle-colored nudge phrases | hybrid_ai_controller.py, nudge_phrases.py |
| `randomize_option_order` | bool | False | Shuffle option order to eliminate position-1 bias | hybrid_ai_controller.py |
| `preflop_range_gate` | bool | False | Bias EV labels for out-of-range preflop hands using range data | hybrid_ai_controller.py |

### Experiment Support

| Field | Type | Default | Description | Consumer |
|-------|------|---------|-------------|----------|
| `guidance_injection` | str | "" | Extra text appended to decision prompts | controllers.py |

## Game Modes

Source of truth: `config/game_modes.yaml` (synced to DB on startup). Factory methods in `prompt_config.py` serve as fallbacks.

| Mode | Description | Overrides from Default |
|------|-------------|----------------------|
| **casual** | Fun, personality-driven poker | None (all defaults) |
| **standard** | Balanced play with GTO awareness | `gto_equity=True` |
| **pro** | GTO-focused, harder AIs | `gto_equity=True`, `gto_verdict=True`, `chattiness=False`, `dramatic_sequence=False`, `tilt_effects=False`, `guidance_injection=EXPLOITATIVE_GUIDANCE` |
| **competitive** | Full GTO + personality + trash talk | `gto_equity=True`, `gto_verdict=True`, `guidance_injection=EXPLOITATIVE_GUIDANCE` |

Resolution order: `from_mode_name()` tries YAML first, falls back to factory methods.

## Experiment Overrides

Experiment JSON configs (`experiments/configs/*.json`) override `prompt_config` per variant. Each variant's `prompt_config` dict is merged over the control config:

```json
{
  "control": {
    "prompt_config": {
      "lean_bounded": true,
      "hand_plan": false
    }
  },
  "variants": [
    {
      "prompt_config": {
        "lean_bounded": true,
        "hand_plan": true
      }
    }
  ]
}
```

Only fields that differ from defaults need to be specified.

## Utility Methods

| Method | Description |
|--------|-------------|
| `to_dict()` | Serialize all fields to a dict |
| `from_dict(data)` | Deserialize with legacy migration and unknown-field handling |
| `disable_all()` | New config with all booleans False (non-booleans unchanged) |
| `enable_all()` | New config with all booleans True |
| `copy(**overrides)` | Clone with field overrides |
| `from_mode_name(mode)` | Resolve by name — YAML first, factory fallback |

## Legacy Field Migration

`from_dict()` auto-migrates these deprecated names:

| Legacy Name | Modern Name |
|-------------|-------------|
| `show_equity_always` | `gto_equity` |
| `show_equity_verdict` | `gto_verdict` |
| `use_minimal_prompt` (True) | `include_personality=False` + `use_simple_response_format=True` |
| `bb_normalized` | Removed (silently dropped) |
| `use_dollar_amounts` | Removed (silently dropped) |

Note: `config/game_modes.yaml` still uses the legacy names (`show_equity_always`, `show_equity_verdict`); they are migrated at load time.
