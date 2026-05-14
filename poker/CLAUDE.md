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
Game State → _build_rule_context() → generate_bounded_options(context, profile, phase, in_range, ...)
  → Check/Fold/Call/Raise generation → Range biasing → Postflop raise limit
  → +EV promotion (profile-aware) → Truncation → Math blocking
  → [optional] apply_composed_nudges() → [optional] apply_emotional_window_shift()
  → [optional] shuffle → Final options (2-4 BoundedOption)
```

### Generator Architecture (Flat)

Each action type is evaluated independently with profile-aware thresholds:
- **CHECK**: Always available when free to act. Check penalty threshold for aggressive profiles. Check promotion styles: `default`, `always`, `conditional`, `suppress_if_raises`.
- **FOLD**: Blocked when equity >> required (profile.fold_equity_multiplier), monster hands, or pot-committed. Honest EV labels from player's perspective (+EV when saving money).
- **CALL**: Blocked when drawing dead (<5%). Three-zone EV via profile thresholds (call_plus_ev, call_marginal).
- **RAISE**: Multiple sizes (small/medium/large). Postflop overrides on OptionProfile. Bluff raises gated on `profile.bluff_frequency > 0`. Honest rationale labeling (-EV raises show "bluff bet").
- **Range gate**: `in_range`/`range_pct` params bias EV labels for out-of-range preflop hands.

### Heads-Up Awareness

In HU (2-player) pots, the bounded options system applies dedicated adjustments:
- Position offsets (`button +0.30`, `big_blind +0.20`)
- Range bias disabled preflop
- Lower monster threshold (`0.75` vs `0.90`)
- HU preflop opens standardized to at least `2x BB`
- Optional `OptionProfile` HU overrides via `heads_up_raise_plus_ev` and `heads_up_raise_neutral`

### Key Types

- `BoundedOption`: dataclass with action, raise_to, rationale, ev_estimate, style_tag
- `OptionProfile`: thresholds for fold/call/raise decisions per play style, with postflop overrides, check promotion, and check penalty
- `EmotionalShift`: state (tilted/shaken/etc), severity (mild/moderate/extreme), intensity
- `STYLE_PROFILES`: dict mapping style names to OptionProfile instances

### Integration Points

- `hybrid_ai_controller.py`: Calls `generate_bounded_options()`, then optionally `apply_composed_nudges()`, `apply_emotional_window_shift()`, and shuffle. Runs the full parent prompt pipeline (persona, chattiness, GTO equity, etc.) and appends bounded options.
- `lean_bounded_controller.py`: Subclass that always uses the lean decision path — bypasses the parent prompt pipeline and sends only cards + numbered options. Selected via `bot_type='lean'` (web) or `player_types[name].type='lean'` (experiments).
- `_get_best_fallback_option()`: Picks best option when LLM returns invalid response
- Profile selection via `style_aware_options` flag on PromptConfig
- Range gate via `preflop_range_gate` flag on PromptConfig

### Spec

Full design doc: `docs/technical/BOUNDED_OPTIONS_DECISION_FRAMEWORK.md`

---

## Related Files

| File | Purpose |
|------|---------|
| `bounded_options.py` | Option generation, flat generator, profiles, emotional shift |
| `hybrid_ai_controller.py` | Full-prompt + bounded-options controller (the `standard` bot type) |
| `lean_bounded_controller.py` | Minimal-prompt subclass of hybrid (the `lean` bot type) |
| `rule_strategies.py` | Shared library of `BUILT_IN_STRATEGIES`, `CHAOS_BOTS`, `RuleConfig`, helpers. Consumed by `rule_based_controller.py` and `rule_bot_controller.py`. |
| `rule_based_controller.py` | Pure rule controller; re-exports `rule_strategies` names for back-compat. |
| `rule_bot_controller.py` | Rule controller with full psychology infrastructure (the `casebot` / `gto_lite` bot types). |
| `moment_analyzer.py` | Drama factor detection and level determination |
| `prompt_manager.py` | DRAMA_CONTEXTS mapping, prompt assembly |
| `controllers.py` | Calls MomentAnalyzer, passes drama_context to prompt |
| `pressure_detector.py` | Post-hand pressure events (uses shared thresholds) |
| `response_validator.py` | Validates dramatic_sequence field |
| `prompt_config.py` | Toggle switches for drama features |

## Bot Controller Lineup

Production bot types selected per-player via `bot_types` (route) or
`player_types[name].type` (experiments):

| `bot_type` | Class | Decision path |
|---|---|---|
| `chaos` | `poker.controllers.AIPlayerController` | Full LLM, full personality, no bounded options |
| `standard` | `poker.hybrid_ai_controller.HybridAIController` | Full prompt pipeline + bounded options (default) |
| `lean` | `poker.lean_bounded_controller.LeanBoundedController` | Minimal prompt, options-only |
| `sharp` | `poker.tiered_bot_controller.TieredBotController` | Solver tables + personality |
| `casebot` / `gto_lite` | `poker.rule_bot_controller.RuleBotController` | Pure rules + psychology |

Legacy values `hybrid` / `tiered` are accepted at the API boundary and
remapped to `standard` / `sharp`. They are intentionally not advertised
in error responses.
