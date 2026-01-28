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
    'routine': "RESPONSE STYLE: Minimal. Skip stage_direction or one brief beat max.",
    'notable': "RESPONSE STYLE: Brief. One or two beats in stage_direction.",
    'high_stakes': "RESPONSE STYLE: Expressive. Build your stage_direction with 2-3 beats.",
    'climactic': "RESPONSE STYLE: Theatrical. Build tension - 3-5 beats, savor the reveal."
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

## Stage Direction Response Format

AI responses use `stage_direction` field instead of legacy `persona_response` + `physical`.

### Format

```json
{
  "stage_direction": [
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

Located in `pressure_detector.py`. Detects post-hand events that affect AI personality elasticity.

Uses `MomentAnalyzer.is_big_pot()` for consistent threshold with drama detection.

---

## Configuration

Drama detection is controlled by `PromptConfig` in `prompt_config.py`:

- `situational_guidance: bool` - Enables drama detection (also controls pot-committed, short-stack guidance)
- `persona_response: bool` - Enables stage_direction instructions in prompt

---

## Related Files

| File | Purpose |
|------|---------|
| `moment_analyzer.py` | Drama factor detection and level determination |
| `prompt_manager.py` | DRAMA_CONTEXTS mapping, prompt assembly |
| `controllers.py` | Calls MomentAnalyzer, passes drama_context to prompt |
| `pressure_detector.py` | Post-hand pressure events (uses shared thresholds) |
| `response_validator.py` | Validates stage_direction field |
| `prompt_config.py` | Toggle switches for drama features |
