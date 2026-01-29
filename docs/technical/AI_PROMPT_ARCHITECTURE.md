# AI Prompt System Architecture

This document describes the different systems that work together to create prompts for the AI poker players.

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                           AI PROMPT SYSTEM ARCHITECTURE                              │
└─────────────────────────────────────────────────────────────────────────────────────┘


  ┌─────────────────────────────────────┐          ┌──────────────────────────────────┐
  │         PROMPT MANAGER              │          │       personalities.json         │
  │       (prompt_manager.py)           │          │         (45+ personas)           │
  │         << UTILITY >>               │          ├──────────────────────────────────┤
  ├─────────────────────────────────────┤          │  • play_style, confidence        │
  │                                     │          │  • attitude, bluff_tendency      │
  │  Templates:                         │          │  • aggression, chattiness        │
  │  ┌─────────────────────────────┐    │          │  • verbal_tics, physical_tics    │
  │  │ 'poker_player' (system msg)│    │          │  • elasticity_config             │
  │  │  • persona_details         │    │          └───────────────┬──────────────────┘
  │  │  • strategy guidance       │    │                          │
  │  │  • response_format         │    │                          │ loads
  │  └─────────────────────────────┘    │                          │
  │  ┌─────────────────────────────┐    │                          ▼
  │  │ 'decision' (user msg)      │    │     ┌─────────────────────────────────────────┐
  │  │  • JSON format instruction │    │     │           AIPokerPlayer                 │
  │  │  • raise guidance          │    │     │         (poker_player.py)               │
  │  └─────────────────────────────┘    │     ├─────────────────────────────────────────┤
  │  ┌─────────────────────────────┐    │     │                                         │
  │  │ 'end_of_hand_commentary'   │    │     │  • persona_prompt() ◄───────────────────┼──┐
  │  │  • reflection prompt       │    │     │  • get_personality_modifier()           │  │
  │  └─────────────────────────────┘    │     │                                         │  │
  │                                     │     │  ┌─────────────────────────────────┐    │  │
  └──────────────┬──────────────────────┘     │  │    OpenAILLMAssistant           │    │  │
                 │                            │  │    (assistants.py)              │    │  │
                 │                            │  │    << CONTAINED >>              │    │  │
                 │ renders                    │  ├─────────────────────────────────┤    │  │
                 │ 'poker_player'             │  │  • system_message ◄─────────────┼────┼──┘
                 │ template                   │  │  • memory[] (conversation)      │    │
                 └────────────────────────────┼──►  • chat(message) ──────────┐    │    │
                                              │  │                            │    │    │
                                              │  └────────────────────────────┼────┘    │
                                              │                               │         │
                                              └───────────────────────────────┼─────────┘
                                                                              │
                 ┌────────────────────────────────────────────────────────────┘
                 │
                 │ calls chat() with assembled prompt
                 │
┌────────────────┴────────────────────────────────────────────────────────────────────┐
│                            AIPlayerController                                        │
│                             (controllers.py)                                         │
│                            << ORCHESTRATOR >>                                        │
├──────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│   decide_action(game_state, player) → assembles complete prompt:                     │
│                                                                                      │
│   1. GAME STATE EXTRACTION                                                           │
│   ┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐           │
│   │  Hole Cards   │ │  Board Cards  │ │   Pot Size    │ │ Cost to Call  │           │
│   └───────────────┘ └───────────────┘ └───────────────┘ └───────────────┘           │
│   ┌───────────────┐ ┌───────────────┐ ┌───────────────┐                              │
│   │  Positions    │ │ Valid Actions │ │ Recent Msgs   │                              │
│   └───────────────┘ └───────────────┘ └───────────────┘                              │
│                                                                                      │
│   2. PSYCHOLOGY STATE (PlayerPsychology)                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐           │
│   │  psychology.get_prompt_section() → Emotional State                  │           │
│   │  • valence, arousal, control, focus • narrative • display_emotion   │           │
│   └─────────────────────────────────────────────────────────────────────┘           │
│   ┌─────────────────────────────────────────────────────────────────────┐           │
│   │  psychology.apply_tilt_effects() → Prompt Modifiers                 │           │
│   │  • intrusive thoughts • tilted advice • degraded strategy           │           │
│   └─────────────────────────────────────────────────────────────────────┘           │
│   ┌─────────────────────────────────────────────────────────────────────┐           │
│   │  psychology.traits → Current Trait Values                           │           │
│   │  • bluff_tendency • aggression • chattiness • emoji_usage           │           │
│   └─────────────────────────────────────────────────────────────────────┘           │
│                                                                                      │
│   3. ADDITIONAL CONTEXT                                                              │
│   ┌───────────────┐ ┌───────────────┐                                               │
│   │ Memory        │ │ Chattiness    │                                               │
│   │ Context       │ │ Guidance      │                                               │
│   ├───────────────┤ ├───────────────┤                                               │
│   │ • session     │ │ • level 0-1   │                                               │
│   │   history     │ │ • should_speak│                                               │
│   │ • opponent    │ │ • style hints │                                               │
│   │   models      │ │               │                                               │
│   └───────────────┘ └───────────────┘                                               │
│                                                                                      │
│   4. GAME CONTEXT FLAGS                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐           │
│   │ big_pot | all_in | heads_up | multi_way_pot | showdown | addressed │           │
│   └─────────────────────────────────────────────────────────────────────┘           │
│                                                                                      │
│   5. RENDER via PromptManager.render_prompt('decision', ...) ◄──────────────────────┼───┐
│                                                                                      │   │
│                                                                                      │   │
│   ┌──────────────────────────────────────────────────────────────────────┐          │   │
│   │                    PlayerPsychology                                  │          │   │
│   │                 (player_psychology.py)                               │          │   │
│   │                 << UNIFIED STATE >>                                  │          │   │
│   ├──────────────────────────────────────────────────────────────────────┤          │   │
│   │                                                                      │          │   │
│   │  Consolidates:                                                       │          │   │
│   │  • ElasticPersonality (dynamic traits with pressure/recovery)        │          │   │
│   │  • EmotionalState (LLM-generated dimensional model + narrative)      │          │   │
│   │  • TiltState (tilt level, source, nemesis tracking)                  │          │   │
│   │                                                                      │          │   │
│   │  Events:                                                             │          │   │
│   │  • apply_pressure_event(event, opponent) → updates elastic + tilt    │          │   │
│   │  • on_hand_complete(...) → updates tilt + generates emotional state  │          │   │
│   │  • recover() → drift traits to anchor, decay tilt                    │          │   │
│   │                                                                      │          │   │
│   │  Prompt Building:                                                    │          │   │
│   │  • get_prompt_section() → emotional state for injection              │          │   │
│   │  • apply_tilt_effects(prompt) → tilt-based prompt modifications      │          │   │
│   │  • get_display_emotion() → avatar emotion selection                  │          │   │
│   └──────────────────────────────────────────────────────────────────────┘          │   │
│                                                                                      │   │
└──────────────────────────────────────────────────────────────────────────────────────┘   │
                 │                                                                         │
                 │ renders 'decision' template                                             │
                 └─────────────────────────────────────────────────────────────────────────┘


                                      │
                                      │ API call with:
                                      │  • system: persona_prompt
                                      │  • memory: conversation history
                                      │  • user: assembled decision prompt
                                      ▼
                          ┌─────────────────────┐
                          │     OpenAI API      │
                          │   (GPT-5-nano or    │
                          │     GPT-4o)         │
                          └──────────┬──────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AI Resilience Layer                                 │
│                         (ai_resilience.py)                                   │
│                    << DECORATOR: @with_ai_fallback >>                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Validation:                      Fallback Strategies:                      │
│   • Parse JSON response            • CONSERVATIVE (check→call→fold)          │
│   • Validate 'action' field        • RANDOM_VALID (weighted random)          │
│   • Check against valid options    • MIMIC_PERSONALITY (trait-based)         │
│   • Fix raise_by vs raise_to       │                                         │
│                                    │                                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
                    ┌────────────────────────────────┐
                    │      RESPONSE FORMAT           │
                    ├────────────────────────────────┤
                    │ Thinking:                      │
                    │  • inner_monologue             │
                    │  • hand_strategy               │
                    │  • player_observations         │
                    │  • hand_strength               │
                    │  • bluff_likelihood            │
                    │ Decision:                      │
                    │  • action (fold/check/call/raise)
                    │  • raise_to (total bet amount) │
                    │ Reaction:                      │
                    │  • stage_direction (beats)     │
                    └────────────────────────────────┘
```

## Component Relationships

```
                    ┌──────────────────────┐
                    │  AIPlayerController  │
                    │   (orchestrator)     │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┬─────────────────┐
              │ uses           │ calls          │ uses            │ owns
              ▼                ▼                ▼                 ▼
    ┌─────────────────┐  ┌───────────┐  ┌─────────────────┐  ┌──────────────────┐
    │  PromptManager  │  │AIPokerPlayer│  │  Game State    │  │PlayerPsychology  │
    │   (utility)     │  │           │  │                 │  │ (unified state)  │
    │                 │  │  contains │  │                 │  │                  │
    │ 'decision'      │  │     │     │  │                 │  │ • Elastic        │
    │  template       │  │     ▼     │  │                 │  │ • Emotional      │
    └─────────────────┘  │┌─────────┐│  └─────────────────┘  │ • Tilt           │
              ▲          ││OpenAI   ││                        └──────────────────┘
              │          ││LLM      ││
              │ uses     ││Assistant││
              │          │└─────────┘│
              │          └───────────┘
              │                │
              │                │ uses
              │                ▼
              │      ┌─────────────────┐
              └──────│  PromptManager  │
                     │   (utility)     │
                     │                 │
                     │ 'poker_player'  │
                     │  template       │
                     └─────────────────┘
```

## Key Files

| Component | Path | Role |
|-----------|------|------|
| Prompt Manager | `poker/prompt_manager.py` | Utility - renders prompt templates |
| Personalities | `poker/personalities.json` | Data - defines 45+ AI personas |
| Player Psychology | `poker/player_psychology.py` | Unified psychological state - elastic traits, emotions, tilt |
| AI Player | `poker/poker_player.py` | Contains assistant, manages persona |
| Controller | `poker/controllers.py` | Orchestrator - assembles prompts, calls AI, owns psychology |
| OpenAI Assistant | `core/assistants.py` | API wrapper - sends/receives from OpenAI |
| Resilience Layer | `poker/ai_resilience.py` | Decorator - validates responses, provides fallbacks |

## Initialization vs Runtime

### At Initialization (game setup)
1. `AIPokerPlayer` loads personality from `personalities.json`
2. `AIPokerPlayer.persona_prompt()` uses `PromptManager` to render `'poker_player'` template
3. System prompt is set on the contained `OpenAILLMAssistant`

### At Runtime (each decision)
1. `AIPlayerController.decide_action()` is called with game state
2. Controller extracts game state and builds context injections
3. Controller uses `PromptManager` to render `'decision'` template with all context
4. Controller calls `player.assistant.chat(prompt)`
5. `OpenAILLMAssistant` sends: system message + memory + user prompt
6. Response is validated by `@with_ai_fallback` decorator
7. Validated action is returned to game

## Data Flow Summary

```
personalities.json ──► AIPokerPlayer ──► persona_prompt() ──► system message
                            │
                            │ contains
                            ▼
                      OpenAILLMAssistant
                            ▲
                            │ chat(user_prompt)
                            │
game_state ──► AIPlayerController ──► render 'decision' ──► user prompt
                    │
                    │ owns PlayerPsychology
                    │
                    └── injects context:
                        ├── psychology.get_prompt_section() (emotional state)
                        ├── psychology.apply_tilt_effects() (tilt modifiers)
                        ├── psychology.traits (bluff, aggression, chattiness)
                        ├── memory context
                        ├── chattiness guidance
                        └── game context flags
```

## Event Flow (Unified Psychology Updates)

```
Pressure Event (bluff_called, bad_beat, etc.)
    │
    ▼
AIPlayerController.psychology.apply_pressure_event(event, opponent)
    │
    ├──► ElasticPersonality.apply_pressure_event() ──► traits shift
    │
    └──► TiltState.apply_pressure_event() ──► tilt increases

Hand Complete
    │
    ▼
AIPlayerController.psychology.on_hand_complete(outcome, amount, ...)
    │
    ├──► TiltState.update_from_hand() ──► tilt updated from outcome
    │
    └──► EmotionalStateGenerator.generate() ──► new emotional state (LLM)

Recovery (between hands)
    │
    ▼
AIPlayerController.psychology.recover()
    │
    ├──► ElasticPersonality.recover_traits() ──► traits drift to anchor
    │
    └──► TiltState.decay() ──► tilt naturally decreases
```
