---
purpose: Architecture of the AI poker player — personality model, decision flow, and LLM integration
type: architecture
created: 2025-06-01
last_updated: 2026-06-03
---

# AI Player System

Dynamic, personality-driven poker opponents. Each AI player carries a persona
(loaded from `poker/personalities.json` or generated on demand), a psychology
state that drifts during play, and an LLM-backed decision loop that picks an
action and produces table talk.

This doc is the **architecture map**: components, data flow, and the seams.
For the field-by-field personality schema (every anchor, its range and meaning),
see [`PERSONALITY_ANCHORS.md`](PERSONALITY_ANCHORS.md). For the psychology
runtime (axes, zones, pressure events), see
[`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md). For how the decision *prompt*
is assembled, see [`AI_PROMPT_ARCHITECTURE.md`](AI_PROMPT_ARCHITECTURE.md).

## Architecture

```
┌─────────────────────┐
│   Web / Console UI  │
└──────────┬──────────┘
           │
┌──────────▼──────────┐     ┌─────────────────────┐
│  AIPlayerController │────►│ PersonalityGenerator│  (lookup / generate persona)
│  (or subclass)      │     └──────────┬──────────┘
└──────────┬──────────┘                │
           │ owns                       ▼
┌──────────▼──────────┐     ┌─────────────────────┐
│  PlayerPsychology   │     │ personalities.json  │
│  (axes / emotion)   │     │   + SQLite cache    │
└──────────┬──────────┘     └─────────────────────┘
           │ injected into prompt
┌──────────▼──────────┐     ┌─────────────────────┐
│   AIPokerPlayer     │────►│  core.llm.Assistant │ ──► provider API
│ (persona + stack)   │     │ (CallType.PLAYER_   │
└─────────────────────┘     │      DECISION)      │
                            └─────────────────────┘
```

## Core Components

### AIPokerPlayer (`poker/poker_player.py`)

Extends `PokerPlayer` with persona state and an LLM assistant. On construction
it loads/generates a personality and builds a `core.llm.Assistant` with the
persona prompt as its system message (`poker_player.py:129`,
`CallType.PLAYER_DECISION`). The provider/model are taken from per-game
`llm_config` (`:121-122`) — the PLAYER_DECISION tier is **set by the user in the
game UI**, not by the Default tier (see the CallType→tier table in the root
`CLAUDE.md`).

Note: this uses `core.llm.Assistant` (the project's provider-agnostic wrapper,
`poker_player.py:7`), **not** a raw OpenAI assistant object.

Key methods:
- `persona_prompt()` (`:331`) — renders the `'poker_player'` template via
  `PromptManager` and appends an example response.
- `get_personality_modifier()` (`:362`) — maps anchors to an archetype play-style
  hint (LAG/TAG/Tricky/Rock); supports the anchors format and a legacy
  `personality_traits` format.

### PersonalityGenerator (`poker/personality_generator.py`)

Resolves a persona by name through a lookup hierarchy: session cache → SQLite →
`personalities.json` → LLM generation if not found. Generated personas are
cached and persisted (with guards against saving reserved/junk names).

### Personality schema (anchors model)

Personas in `poker/personalities.json` use an **anchors** block plus skill and
economy metadata. The historical "5-trait poker-native model"
(tightness/aggression/confidence/composure/table_talk) **never existed in this
file** — it was doc fiction. The real top-level keys (verified by enumerating
every persona, 62 total) are:

| Key | What it carries |
|-----|-----------------|
| `anchors` | Baseline psychology axes (see below + `PERSONALITY_ANCHORS.md`) |
| `skill` | Strategy tier label (e.g. `weak_reg`, `reg`) driving the solver/bot loadout |
| `play_style`, `default_confidence`, `default_attitude` | Persona flavor / prompt text |
| `bankroll_knobs` | Cash economy: `starting_bankroll`, `bankroll_rate`, `buy_in_multiplier`, `stake_comfort_zone` |
| `staker_profile` / `borrower_profile` | Backing/staking economy willingness + terms |
| `verbal_tics`, `physical_tics` | Character expression snippets |
| `id` | Stable persona id |
| `archetype`, `rule_strategy`, `fish_leak`, `spot_tendencies`, `adaptive_overbet`, `nickname`, `visual_identity` | Optional bot-behavior / presentation extras (not on every persona) |

The `anchors` block holds 10 axis baselines (the first 9 are present on every
persona; `self_belief` is optional and defaults to `0.5` —
`psychology_model.py:161`):

`baseline_aggression`, `baseline_looseness`, `ego`, `poise`, `expressiveness`,
`risk_identity`, `adaptation_bias`, `baseline_energy`, `recovery_rate`,
`self_belief`.

Field-by-field semantics and ranges: **[`PERSONALITY_ANCHORS.md`](PERSONALITY_ANCHORS.md)**.

> There is no `elasticity_config` / `personality_traits` block in the shipped
> `personalities.json` (both confirmed absent). The runtime psychology system
> derives behavior from `anchors`, not a stored trait dict.

### PlayerPsychology (`poker/player_psychology.py`)

The unified runtime psychology owned by the controller. It models **axes**
(confidence, composure, energy, plus the anchored baselines) and an emotional
state, drifting them via pressure events and recovery between hands. See
[`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md).

Two compatibility surfaces are worth flagging because they are easy to mistake
for the old model:
- `psychology.traits` (`player_psychology.py`) still returns a dict keyed
  `tightness/aggression/confidence/composure/table_talk` — but its docstring
  says *"backward compat"*; these are **derived views** computed from the axes,
  not a persisted personality schema.
- `apply_tilt_effects(prompt)` and `apply_zone_effects(prompt)` are the methods
  that fold low-composure / zone modifiers into the prompt (there is **no**
  `apply_composure_effects` method — that name in older docs is wrong).

### Controllers (`poker/controllers.py` + subclasses)

`AIPlayerController` orchestrates a decision: translate global game state to the
player's perspective, build context (recent actions, chat, psychology section),
render the `'decision'` prompt, call the assistant, and validate the response.
Several bot types subclass or replace this path (`standard` →
`HybridAIController`, `lean` → `LeanBoundedController`, `sharp` →
`TieredBotController`); see `poker/CLAUDE.md` "Bot Controller Lineup".

## Decision Flow

**Initialization (game setup)**
1. Persona resolved via `PersonalityGenerator` (cache → DB → JSON → generate).
2. `AIPokerPlayer.persona_prompt()` renders the `'poker_player'` template.
3. The rendered prompt becomes the system message of a `core.llm.Assistant`
   (`CallType.PLAYER_DECISION`, per-game provider/model).

**Per-turn decision**
1. Controller receives game state, extracts the player view + valid actions.
2. Controller injects psychology (`get_prompt_section()`, then
   `apply_tilt_effects` / `apply_zone_effects`) and other context.
3. Controller renders the `'decision'` template and calls
   `assistant.chat(..., json_format=True)` (`poker_player.py:480`).
4. The response is validated and repaired by the resilience layer (see below);
   the structured action (fold/check/call/raise + table talk) returns to the game.

## Response Format & Resilience

The LLM returns structured JSON: a thinking block (`inner_monologue`,
`hand_strategy`, `player_observations`, `hand_strength`, `bluff_likelihood`), a
decision (`action`, `raise_to`), and a `dramatic_sequence` of reaction beats.
(A simplified `{action, raise_to}` format is available via
`use_simple_response_format`.)

Validation and fallback live in `poker/ai_resilience.py`: the
`@with_ai_fallback` decorator (`ai_resilience.py:374`) parses/validates the
response and, on failure, substitutes a deterministic action. Fallback
strategies (`AIFallbackStrategy`, `:39`): `CONSERVATIVE` (check→call→fold),
`RANDOM_VALID`, `MIMIC_PERSONALITY` (trait-weighted).

## Persistence

Persona config and psychology state serialize with the player; the assistant's
conversation memory round-trips via `Assistant.to_dict()` /
`Assistant.from_dict()` (`poker_player.py:161,201`). Personas persist across
games in SQLite.

## Debugging

- `[PersonalityGenerator]`-tagged logs trace persona lookup/generation.
- Player-decision LLM calls are logged to `api_usage` (and optionally captured to
  `prompt_captures`) with `call_type='player_decision'`.
