---
purpose: How AI player prompts are assembled, from personality config through the LLM assistant layer
type: architecture
created: 2026-01-04
last_updated: 2026-06-03
---

# AI Prompt System Architecture

How the pieces fit together to build the prompt an AI poker player sends to the
LLM each turn: persona config → system prompt, game state + psychology →
decision prompt, response → validated action.

Related: persona schema in [`PERSONALITY_ANCHORS.md`](PERSONALITY_ANCHORS.md),
player-system overview in [`AI_PLAYER_SYSTEM.md`](AI_PLAYER_SYSTEM.md),
psychology runtime in [`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md), and the
prompt toggle catalog in [`PROMPT_CONFIG_REFERENCE.md`](PROMPT_CONFIG_REFERENCE.md).

## System Overview

```
┌─────────────────────────────────────┐          ┌──────────────────────────────────┐
│         PROMPT MANAGER              │          │       personalities.json         │
│       (prompt_manager.py)           │          │         (62 personas)            │
│         << UTILITY >>               │          ├──────────────────────────────────┤
├─────────────────────────────────────┤          │  • play_style, confidence,       │
│  Templates (YAML in poker/prompts/):│          │    attitude                      │
│  ┌─────────────────────────────┐    │          │  • anchors{} (10 psychology      │
│  │ 'poker_player' (system msg)│    │          │    axis baselines)               │
│  │  • persona_details         │    │          │  • skill, bankroll_knobs,        │
│  │  • response_format         │    │          │    staker_profile                │
│  └─────────────────────────────┘    │          │  • verbal_tics, physical_tics    │
│  ┌─────────────────────────────┐    │          └───────────────┬──────────────────┘
│  │ 'decision' (user msg)      │    │                          │ loads
│  └─────────────────────────────┘    │                          ▼
│  ┌─────────────────────────────┐    │     ┌─────────────────────────────────────────┐
│  │ 'end_of_hand_commentary'   │    │     │           AIPokerPlayer                 │
│  └─────────────────────────────┘    │     │         (poker_player.py)               │
│  (templates loaded from *.yaml      │     ├─────────────────────────────────────────┤
│   via yaml.safe_load, hot-reload    │     │  • persona_prompt() ◄───────────────────┼──┐
│   in dev)                           │     │  • get_personality_modifier()           │  │
└──────────────┬──────────────────────┘     │                                         │  │
               │                            │  ┌─────────────────────────────────┐    │  │
               │                            │  │  core.llm.Assistant             │    │  │
               │ renders                    │  │  (core/llm/assistant.py)        │    │  │
               │ 'poker_player'             │  │  << CONTAINED >>                │    │  │
               │ template                   │  ├─────────────────────────────────┤    │  │
               └────────────────────────────┼──►  • system_prompt ◄──────────────┼────┼──┘
                                            │  │  • memory[] (conversation)      │    │
                                            │  │  • chat(message) ──────────┐    │    │
                                            │  └────────────────────────────┼────┘    │
                                            └───────────────────────────────┼─────────┘
                                                                            │
               ┌────────────────────────────────────────────────────────────┘
               │ calls chat() with assembled prompt
               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                            AIPlayerController                                        │
│                             (controllers.py)                                         │
│                            << ORCHESTRATOR >>                                        │
├──────────────────────────────────────────────────────────────────────────────────────┤
│   decide_action() → assembles complete prompt:                                       │
│                                                                                      │
│   1. GAME STATE EXTRACTION                                                           │
│      hole cards • board • pot • cost to call • positions • valid actions • recent    │
│                                                                                      │
│   2. PSYCHOLOGY STATE (PlayerPsychology)                                             │
│      • get_prompt_section()  → emotional state text                                  │
│      • apply_tilt_effects()  → low-composure modifications (intrusive thoughts,      │
│                                degraded strategy)                                     │
│      • apply_zone_effects()  → zone-based strategy modifiers                          │
│      • derived axes (confidence/composure/energy from anchors)                       │
│                                                                                      │
│   3. ADDITIONAL CONTEXT                                                              │
│      memory context (session history, opponent models) • chattiness guidance         │
│                                                                                      │
│   4. GAME CONTEXT FLAGS                                                              │
│      big_pot | all_in | heads_up | multi_way | showdown | addressed                  │
│                                                                                      │
│   5. RENDER via PromptManager.render_decision_prompt(...)                            │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ API call with:
                                      │  • system: persona_prompt
                                      │  • memory: conversation history
                                      │  • user:   assembled decision prompt
                                      ▼
                          ┌─────────────────────┐
                          │  core.llm.Assistant │  → provider API (per-game model;
                          │  (CallType.         │     PLAYER_DECISION tier set by
                          │   PLAYER_DECISION)  │     user in game UI)
                          └──────────┬──────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AI Resilience Layer (ai_resilience.py)              │
│                    << DECORATOR: @with_ai_fallback >>                        │
├─────────────────────────────────────────────────────────────────────────────┤
│   Validation:                      Fallback Strategies (AIFallbackStrategy): │
│   • Parse JSON response            • CONSERVATIVE (check→call→fold)          │
│   • Validate 'action' field        • RANDOM_VALID (weighted random)          │
│   • Check against valid options    • MIMIC_PERSONALITY (trait-based)         │
│   • Fix raise_by vs raise_to                                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ▼
                    ┌────────────────────────────────┐
                    │      RESPONSE FORMAT           │
                    │ Thinking: inner_monologue,     │
                    │  hand_strategy,                │
                    │  player_observations,          │
                    │  hand_strength, bluff_likelihood
                    │ Decision: action, raise_to     │
                    │ Reaction: dramatic_sequence    │
                    └────────────────────────────────┘
```

## Model selection

The default LLM tier is **groq `llama-3.1-8b-instant`** (`DEFAULT_MODEL` /
`DEFAULT_PROVIDER`, `core/llm/config.py:33-34`) — a cheap, fast model, since the
default opponent is a solver/tiered bot that only calls the LLM for table talk.
The **PLAYER_DECISION** tier specifically is per-game (chosen by the user in the
game UI), so an AI player may run a different model than the Default tier. The
full CallType→tier mapping lives in the root `CLAUDE.md`.

## Key Files

| Component | Path | Role |
|-----------|------|------|
| Prompt Manager | `poker/prompt_manager.py` | Loads YAML templates from `poker/prompts/`, renders prompts |
| Templates | `poker/prompts/*.yaml` | `poker_player`, `decision`, `end_of_hand_commentary`, plus quick-chat / post-round variants |
| Personalities | `poker/personalities.json` | 62 personas; `anchors` block + skill/economy metadata |
| Player Psychology | `poker/player_psychology.py` | Axes/emotion runtime; `get_prompt_section`, `apply_tilt_effects`, `apply_zone_effects` |
| AI Player | `poker/poker_player.py` | Holds the `core.llm.Assistant`, renders persona prompt |
| Controller | `poker/controllers.py` | Orchestrates: extracts state, injects psychology, renders `decision` |
| LLM Assistant | `core/llm/assistant.py` | Provider-agnostic chat wrapper (class `Assistant`) |
| Resilience Layer | `poker/ai_resilience.py` | `@with_ai_fallback` — validates responses, deterministic fallback |

> Corrections from earlier revisions of this doc: the assistant class is
> `Assistant` in `core/llm/assistant.py` (there is **no** `core/assistants.py`
> or `OpenAILLMAssistant`); the persona count is **62**, not "45+"; personas
> carry an `anchors` block, **not** a 5-trait model or an `elasticity_config`
> (both absent from `personalities.json`); the default model is groq
> llama-3.1-8b-instant, not GPT-5-nano/GPT-4o.

## Initialization vs Runtime

**At initialization (game setup)**
1. `AIPokerPlayer` resolves a personality (`personalities.json` / generator).
2. `AIPokerPlayer.persona_prompt()` renders the `'poker_player'` template.
3. The result is set as the system prompt on the contained `core.llm.Assistant`.

**At runtime (each decision)**
1. `AIPlayerController.decide_action()` runs with current game state.
2. Controller extracts state and builds context injections.
3. Controller calls `PromptManager.render_decision_prompt(...)` (`controllers.py:1888`).
4. Controller injects psychology: `get_prompt_section()`, then
   `apply_tilt_effects()` (`controllers.py:1079,1085`).
5. Controller calls `player.assistant.chat(message, json_format=True)`
   (`poker_player.py:480`) — sends system + memory + user prompt.
6. Response validated by `@with_ai_fallback`; validated action returns to the game.

## Event Flow (Psychology Updates)

```
Pressure Event (bluff_called, bad_beat, ...)
    └──► psychology.apply_pressure_event(event, opponent) ──► axes shift
         (poise/recovery_rate filter the magnitude)

Hand Complete
    └──► psychology.on_hand_complete(outcome, amount, ...)
         ├──► composure update (pressure source, nemesis)
         └──► emotional state generation:
              baseline mood (from axes vs anchors)
              + reactive spike (outcome/amount, amplified by low composure)
              → LLM narrates the pre-computed dimensions

Recovery (between hands)
    └──► psychology.recover() ──► axes drift toward anchors; emotion decays
         toward the personality-specific resting state
```

Method names verified in `poker/player_psychology.py`
(`apply_pressure_event`, `on_hand_complete`, `recover`, `get_prompt_section`,
`apply_tilt_effects`, `apply_zone_effects`). The detailed axis math lives in
[`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md).
