---
purpose: Architecture overview of how the project uses LLMs — the class layers, how they inherit/compose, where they're invoked, and how to extend them.
type: architecture
created: 2026-06-06
last_updated: 2026-06-06
---

# LLM Architecture Overview

Orientation for anyone (human or agent) who needs to understand, use, or extend
the LLM system. For the exhaustive per-call inventory + tier defaults + prod
latency, see the companion **`LLM_CALL_MAP.md`**. This doc is the *shape*: the
layers, the class hierarchy, and the extension recipes.

## What LLMs are used for

Two broad jobs:

1. **In-game AI behavior** — the celebrity/persona opponents. Decisions, table
   talk, narration, lobby flavor, chat suggestions.
2. **Platform / design-time** — personality generation, coaching, experiment
   design & analysis, avatar image generation, debug replay.

**Key design philosophy (decisions):** the LLM does **not** reason about poker
math. A solver computes an EV-labeled menu of legal actions
(`poker/bounded_options.py`); the LLM only *picks* from it and supplies
personality/table-talk. This keeps strategy correct and the model cheap. See
`docs/technical/BOUNDED_OPTIONS_DECISION_FRAMEWORK.md`.

## The three layers

```
  ┌─────────────────────────────────────────────────────────────┐
  │ CONSUMERS (poker/, flask_app/, cash_mode/, experiments/)      │
  │  AI controllers, ExpressionGenerator, CommentaryGenerator,    │
  │  LLMCategorizer, CoachAssistant, PersonalityGenerator, images │
  └───────────────┬─────────────────────────────────────────────┘
                  │ build an Assistant (stateful) or LLMClient (stateless),
                  │ choosing a TIER via settings.get_<tier>_model/provider()
  ┌───────────────▼─────────────────────────────────────────────┐
  │ ABSTRACTION (core/llm/)                                       │
  │  Assistant ── wraps ──▶ LLMClient ── wraps ──▶ LLMProvider    │
  │  + CallType, UsageTracker, SpendGate, settings, config        │
  └───────────────┬─────────────────────────────────────────────┘
                  │ provider-specific HTTP via a shared httpx client
  ┌───────────────▼─────────────────────────────────────────────┐
  │ PROVIDERS (core/llm/providers/) — one class per vendor        │
  │  OpenAI · Groq · xAI · Anthropic · DeepSeek · Mistral ·       │
  │  Google · Pollinations · Runware (images)                     │
  └─────────────────────────────────────────────────────────────┘
```

### Layer 1 — Providers (`core/llm/providers/`)

`LLMProvider(ABC)` (`base.py`) is the vendor interface. Concrete subclasses:
`OpenAIProvider`, `GroqProvider`, `XAIProvider`, `AnthropicProvider`,
`DeepSeekProvider`, `MistralProvider`, `GoogleProvider`, `PollinationsProvider`,
`RunwareProvider` (images). Each implements the abstract methods: `complete()`,
`generate_image()`, `provider_name`, `model`, and the `extract_*` response
adapters (`extract_usage/content/finish_reason/image_url/request_id`).

- Groq/xAI/DeepSeek/Mistral reuse the **OpenAI-compatible** SDK with a different
  `base_url`.
- All share one pooled `httpx` client (`http_client.py`) and are constructed with
  **`max_retries=0`** — retries are owned by the app layer (see LLMClient), so the
  SDK doesn't multiply the per-call timeout.
- `XAIProvider` resolves toggleable models: `grok-4-fast` + `reasoning_effort
  ="minimal"` → fast non-reasoning variant; anything else → the slow reasoning
  variant. (This is the #1 latency footgun — see LLM_CALL_MAP.)

### Layer 2 — The abstraction (`core/llm/`)

- **`LLMClient`** (`client.py`) — the stateless workhorse for one-off completions.
  Owns the **retry loop** (`max_retries=2` → 3 attempts, retryable-error aware),
  the per-call **timeout**, the **budget gate** (`SpendGate`), and **usage
  tracking**. Construct with `provider=`, `model=`, optional `reasoning_effort=`
  and `default_timeout=`. Call `.complete(messages, json_format=, call_type=, ...)`
  → `LLMResponse` (`response.py`).
- **`Assistant`** (`assistant.py`) — stateful wrapper that **composes an
  `LLMClient`** plus `ConversationMemory` (`conversation.py`). This is what AI
  players use so a hand's dialogue has continuity. `.chat()` / `.chat_full()`.
- **`CallType`** (`tracking.py`) — the enum tagging every call (`PLAYER_DECISION`,
  `COMMENTARY`, `CATEGORIZATION`, …). Drives tier selection *by convention* (the
  call site picks the matching getter) and cost attribution.
- **`UsageTracker`** (`tracking.py`) — writes every call to the `api_usage` table
  (tokens, latency, model, provider, game/owner context, status). This is the
  source for cost/latency analysis (and the LLM_CALL_MAP latency numbers).
- **`SpendGate`** (`budget.py`) — a per-turn spend kill-switch; cosmetic call types
  are shed first when the cap trips, decisions never.
- **`settings.py` / `config.py`** — tier resolution. `config.py` holds the code
  defaults; `settings.get_<tier>_provider()/model()` read the DB `app_settings`
  first (admin-editable, no deploy) and fall back to config.

#### Tiers

Five tiers, chosen at each call site by which getter builds the client:
**DEFAULT** (commentary/narration), **FAST** (player-read flavor),
**NANO** (mechanical never-read: cleanup, categorization), **ASSISTANT**
(reasoning: coaching/experiments/personality), **IMAGE**. Full table + defaults
in `LLM_CALL_MAP.md` and the CLAUDE.md "Model Tiers" section.

### Layer 3 — Consumers

#### AI player controllers (`poker/`)

The opponent's behavior is a controller. Inheritance:

```
AIPlayerController            (controllers.py)        bot_type "chaos"  — full LLM, full personality
 ├─ HybridAIController        (hybrid_ai_controller.py)         "standard" — prompt pipeline + bounded options (DEFAULT)
 │   └─ LeanBoundedController (lean_bounded_controller.py)      "lean"     — minimal prompt, options-only
 ├─ TieredBotController       (tiered_bot_controller.py)        "sharp"    — solver tables + LLM narration layer
 │   └─ BaselineSolverBot     (tiered_bot_controller.py)
 └─ RuleBotController         (rule_bot_controller.py)          "casebot"/"gto_lite" — pure rules + psychology, NO LLM decision
```

(`ConsolePlayerController` = human; `RuleBasedController` = pure rules, no LLM.)

- The **decision** call goes through the player's `Assistant` (built in
  `AIPokerPlayer.__init__`, `poker/poker_player.py`, `call_type=PLAYER_DECISION`).
- `bot_type` → class is resolved in `flask_app/handlers/tiered_factory.py`. The
  `sharp` (tiered) bot is the **default opponent**: its action is solver-decided,
  then an **`ExpressionGenerator`** (`poker/strategy/expression_generator.py`) adds
  table-talk via a DEFAULT-tier LLM call (`COMMENTARY`).
- Full lineup + decision paths: `poker/CLAUDE.md` ("Bot Controller Lineup").

#### Other LLM-using services

| Class / module | Job | Tier | CallType |
|---|---|---|---|
| `ExpressionGenerator` (`poker/strategy/`) | sharp-bot table-talk narration + beat cleanup | DEFAULT / NANO | COMMENTARY / NARRATION_CLEANUP |
| `CommentaryGenerator` (`poker/memory/`) | end-of-hand commentary | DEFAULT / NANO | COMMENTARY / NARRATION_CLEANUP |
| `LLMCategorizer` (`core/llm_categorizer.py`) | classify emotion/action into buckets | NANO | CATEGORIZATION |
| `CoachAssistant` (`flask_app/services/`) | poker coaching | ASSISTANT | COACHING |
| `PersonalityGenerator` (`poker/personality_generator.py`) | generate personas | ASSISTANT | PERSONALITY_GENERATION/PREVIEW |
| `cash_mode/vice_narration.py`, `side_hustle_narration.py` | lobby flavor (world ticker) | FAST | VICE/SIDE_HUSTLE_NARRATION |
| `poker/character_images.py`, `user_avatar_service.py` | avatars | IMAGE | IMAGE_GENERATION/DESCRIPTION |

**Where these are invoked + which block a player vs run in the background:** see
`LLM_CALL_MAP.md` (the action-sync vs action-incidental classification).

## How to add a new LLM call

1. Pick a tier by the work's nature: player-reads-it-and-quality-matters → FAST
   (flavor) or DEFAULT (prose); mechanical/never-read → NANO; reasoning →
   ASSISTANT; image → IMAGE.
2. Build the client with that tier's getters:
   ```python
   from core.llm import LLMClient, CallType
   from core.llm.settings import get_nano_model, get_nano_provider
   client = LLMClient(
       provider=get_nano_provider(), model=get_nano_model(),
       reasoning_effort="minimal",        # mechanical/latency-sensitive → minimal
       default_timeout=FAST_LLM_TIMEOUT_SECONDS,  # always bound in-request calls
   )
   resp = client.complete(messages=[...], json_format=True,
                          call_type=CallType.CATEGORIZATION, game_id=...)
   ```
   For a stateful conversation (an AI player), use `Assistant` instead.
3. If it's a genuinely new kind of work, add a `CallType` (`tracking.py`), map it
   to a tier in CLAUDE.md, and add a row to `LLM_CALL_MAP.md`.
4. **Always pass a `default_timeout`** for anything in a user's request or the
   world-ticker greenlet (the shared httpx default is 600s).

## How to add a new provider

1. Subclass `LLMProvider` in `core/llm/providers/<vendor>.py`; implement the
   abstract methods (OpenAI-compatible vendors can mostly mirror `groq.py`).
   Construct the SDK client with `max_retries=0` + the shared `http_client`.
2. Register it in `LLMClient._create_provider` (`client.py`) and in
   `config.py`: `AVAILABLE_PROVIDERS`, `PROVIDER_MODELS`, `PROVIDER_CAPABILITIES`,
   and (to enable models at DB init) `DEFAULT_ENABLED_MODELS`.

## Testing

LLM/network side-paths are disabled suite-wide in `tests/conftest.py`; use
`make_openai_response` / `mock_openai_response` to fake responses. Never hit a
real provider in tests. See `tests/CLAUDE.md`.

## Related docs
- `docs/technical/LLM_CALL_MAP.md` — every call site, tier, sync/async, latency.
- `docs/technical/BOUNDED_OPTIONS_DECISION_FRAMEWORK.md` — how decisions are bounded.
- `poker/CLAUDE.md` — bot controller lineup + decision paths.
- CLAUDE.md "LLM Module" + "Model Tiers" — quick reference.
