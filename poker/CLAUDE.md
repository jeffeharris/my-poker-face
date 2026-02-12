# Poker Module - CLAUDE.md

## Drama Detection System

The drama detection system calibrates AI response intensity based on game situation. Located in `moment_analyzer.py`.

### Flow

```
Game State → MomentAnalyzer.analyze() → MomentAnalysis(level, factors, tone) → Prompt text
```

### Drama Factors

Factors are boolean signals detected from game state:

| Factor | Condition | Threshold |
|--------|-----------|-----------|
| `all_in` | Player going all-in or desperate | `cost >= stack` OR `stack <= 3 BB` |
| `big_pot` | Pot significant vs player stack | `pot > 50% of stack` (or 75% avg) |
| `big_bet` | Facing a large bet | `cost_to_call > 10 BB` |
| `showdown` | On the river | 5 community cards dealt |
| `heads_up` | Only two players remain | 2 active players |
| `huge_raise` | Opponent made massive raise | `raise > 3x pot` |
| `late_stage` | Tournament pressure | `≤3 players` AND `avg < 15 BB` |

### Drama Levels

Determined by `_determine_level()` based on which factors are present:

```
climactic   = all_in OR (big_pot AND showdown)
high_stakes = 2+ factors (not climactic)
notable     = 1 factor
routine     = 0 factors
```

### Drama Tones

Tones add context-aware emotional coloring based on hand strength:

| Tone | Condition |
|------|-----------|
| `triumphant` | Climactic moment + strong hand (70%+ equity) |
| `confident` | Notable+ moment + good hand (50%+ equity) |
| `desperate` | Short stack OR weak hand (< 30% equity) in high-stakes moment |
| `neutral` | Default (no special conditions) |

Determined by `_determine_tone()` based on drama level and hand equity.

### Prompt Text Mapping

In `prompt_manager.py`, levels map to response style instructions:

```python
DRAMA_CONTEXTS = {
    'routine': "RESPONSE STYLE: Minimal. Skip dramatic_sequence or one brief beat max.",
    'notable': "RESPONSE STYLE: Brief. One or two beats in dramatic_sequence.",
    'high_stakes': "RESPONSE STYLE: Expressive. Build your dramatic_sequence with 2-3 beats.",
    'climactic': "RESPONSE STYLE: Theatrical. Build tension in dramatic_sequence - 3-5 beats, savor the reveal."
}

TONE_MODIFIERS = {
    'neutral': "",
    'confident': " Channel quiet confidence - you know you have the goods.",
    'desperate': " Show the pressure - this is do-or-die, make it feel that way.",
    'triumphant': " Savor the moment - you've got them right where you want them."
}
```

Tone modifiers are appended to drama context for nuanced response guidance.

### Thresholds (Constants)

All thresholds are defined in `MomentAnalyzer` class:

```python
BIG_POT_RATIO = 0.5              # Pot > 50% of player's stack
BIG_POT_AVG_RATIO = 0.75         # Pot > 75% of average stack
SHORT_STACK_BB = 3               # Less than 3 BB is desperate
BIG_BET_BB = 10                  # Bet > 10 BB is significant
HUGE_RAISE_POT_MULTIPLIER = 3.0  # Raise > 3x pot is dramatic
LATE_STAGE_PLAYERS = 3           # 3 or fewer players for late stage
LATE_STAGE_AVG_BB = 15           # Average stack < 15 BB for late stage
```

---

## Dramatic Sequence Response Format

AI responses use the `dramatic_sequence` field for visible reactions and table talk.

### Format

```json
{
  "dramatic_sequence": [
    "*narrows eyes*",
    "Your move, buddy.",
    "*pushes chips forward slowly*"
  ]
}
```

- **Actions**: Wrapped in `*asterisks*` - displayed as italics
- **Speech**: Plain text
- **Beat count**: Scales with drama level (0-1 routine, 3-5 climactic)

### Frontend Display

- `FloatingChat`: Actions fade in, speech types out character-by-character
- `Chat`/`ActivityFeed`: Simple styling (no animations for history)

---

## Pressure Detection System

Located in `pressure_detector.py`. Detects post-hand events that affect AI psychology axes (confidence, composure, energy).

Uses `MomentAnalyzer.is_big_pot()` for consistent threshold with drama detection.

---

## Configuration

Drama detection is controlled by `PromptConfig` in `prompt_config.py`:

- `situational_guidance: bool` - Enables drama detection (also controls pot-committed, short-stack guidance)
- `dramatic_sequence: bool` - Enables dramatic_sequence instructions in prompt

---

## Bounded Options System

The bounded options system generates EV-labeled option menus for AI decisions. Located in `bounded_options.py`.

### Flow

```
Game State → _build_rule_context() → generate_bounded_options(context, profile)
  → Case Matrix (F1-F4/B1-B6) → Position → Play Style → Stack Depth → Math Blocking
  → [optional] apply_emotional_window_shift() → Final options (2-4 BoundedOption)
```

### Case Matrix

**Free to act** (cost_to_call = 0):
- F1 Monster (90%+), F2 Strong (65-90%), F3 Decent (40-65%), F4 Weak (<40%)

**Facing bet** (cost_to_call > 0):
- B1 Monster (90%+), B2 Crushing (>1.7× req), B3 Profitable (1.0-1.7×), B4 Marginal (0.85-1.0×), B5 Weak (<0.85×), B6 Dead (<5%)

### Key Types

- `BoundedOption`: dataclass with action, raise_to, rationale, ev_estimate, style_tag
- `OptionProfile`: thresholds for fold/call/raise decisions per play style
- `EmotionalShift`: state (tilted/shaken/etc), severity (mild/moderate/extreme), intensity
- `STYLE_PROFILES`: dict mapping style names to OptionProfile instances

### Integration Points

- `hybrid_ai_controller.py`: Calls `generate_bounded_options()` then `apply_emotional_window_shift()`
- `_get_best_fallback_option()`: Picks best option when LLM returns invalid response
- Profile selection via `style_aware_options` flag on PromptConfig

### Spec

Full design doc: `docs/technical/BOUNDED_OPTIONS_DECISION_FRAMEWORK.md`

---

## Related Files

| File | Purpose |
|------|---------|
| `bounded_options.py` | Option generation, case matrix, profiles, emotional shift |
| `hybrid_ai_controller.py` | Lean prompt assembly, option integration, fallback logic |
| `moment_analyzer.py` | Drama factor detection and level determination |
| `prompt_manager.py` | DRAMA_CONTEXTS mapping, prompt assembly |
| `controllers.py` | Calls MomentAnalyzer, passes drama_context to prompt |
| `pressure_detector.py` | Post-hand pressure events (uses shared thresholds) |
| `response_validator.py` | Validates dramatic_sequence field |
| `prompt_config.py` | Toggle switches for drama features |
