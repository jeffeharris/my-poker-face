# LLM Refactor & Cost Tracking Plan

**Status**: Draft
**Goal**: Unified LLM abstraction with built-in cost tracking, replacing `core/assistants.py`

---

## Overview

Replace the legacy `OpenAILLMAssistant` with a clean architecture that:
1. Separates LLM calls from conversation memory
2. Tracks all API usage with context (game, user, call type)
3. Supports multiple providers (OpenAI now, Anthropic/Groq later)

---

## File Structure

```
core/
├── llm/
│   ├── __init__.py              # Exports: LLMClient, LLMResponse, UsageTracker
│   ├── client.py                # LLMClient - unified entry point
│   ├── response.py              # LLMResponse, ImageResponse dataclasses
│   ├── tracking.py              # UsageTracker - persists to DB
│   ├── conversation.py          # ConversationMemory - stateful wrapper
│   └── providers/
│       ├── __init__.py
│       ├── base.py              # LLMProvider ABC
│       └── openai.py            # OpenAIProvider implementation
│
├── assistants.py                # DELETE after migration
└── llm_categorizer.py           # UPDATE to use LLMClient
```

---

## Database Schema

Add to `poker/persistence.py`:

```sql
CREATE TABLE api_usage (
    id INTEGER PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Context
    game_id TEXT,
    owner_id TEXT,
    player_name TEXT,
    hand_number INTEGER,
    call_type TEXT,
    prompt_template TEXT,

    -- Provider/Model
    provider TEXT NOT NULL,
    model TEXT NOT NULL,

    -- Tokens
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,

    -- Images (for DALL-E calls)
    image_count INTEGER DEFAULT 0,
    image_size TEXT,

    -- Performance & Status
    latency_ms INTEGER,
    status TEXT,                -- 'ok', 'error', 'fallback'
    finish_reason TEXT,
    error_code TEXT,
    fallback_used BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_api_usage_owner ON api_usage(owner_id);
CREATE INDEX idx_api_usage_game ON api_usage(game_id);
CREATE INDEX idx_api_usage_created ON api_usage(created_at);
CREATE INDEX idx_api_usage_call_type ON api_usage(call_type);
```

---

## Key Interfaces

### LLMClient

```python
class LLMClient:
    def __init__(
        self,
        provider: str = "openai",
        model: str = None,
        tracker: UsageTracker = None
    ): ...

    def complete(
        self,
        messages: List[dict],
        json_format: bool = False,
        temperature: float = 1.0,
        max_tokens: int = 2800,
        # Tracking context
        call_type: str = None,
        game_id: str = None,
        owner_id: str = None,
        player_name: str = None,
        prompt_template: str = None,
    ) -> LLMResponse: ...

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        call_type: str = None,
        **context
    ) -> ImageResponse: ...
```

### LLMResponse

```python
@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: float = 0
    finish_reason: str = ""
    raw_response: Any = None
```

### ConversationMemory

```python
class ConversationMemory:
    def __init__(self, system_prompt: str = "", max_messages: int = 15): ...
    def add_user(self, content: str): ...
    def add_assistant(self, content: str): ...
    def get_messages(self) -> List[dict]: ...
    def clear(self): ...
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "ConversationMemory": ...
```

---

## Call Types

| `call_type` | Location | Context Available |
|-------------|----------|-------------------|
| `player_decision` | `poker/controllers.py` | game_id, player_name, owner_id |
| `commentary` | `poker/memory/commentary_generator.py` | game_id, player_name |
| `chat_suggestion` | `flask_app/routes/stats_routes.py` | game_id, owner_id |
| `targeted_chat` | `flask_app/routes/stats_routes.py` | game_id, owner_id |
| `personality_generation` | `poker/personality_generator.py` | owner_id |
| `personality_preview` | `flask_app/routes/personality_routes.py` | owner_id |
| `image_generation` | `poker/character_images.py` | (none - one-time) |
| `image_description` | `poker/character_images.py` | (none - one-time) |

---

## Migration Checklist

### Phase 1: Create new infrastructure
- [ ] Create `core/llm/` package structure
- [ ] Implement `LLMResponse` dataclass
- [ ] Implement `LLMProvider` ABC and `OpenAIProvider`
- [ ] Implement `LLMClient`
- [ ] Implement `UsageTracker` (with DB persistence)
- [ ] Implement `ConversationMemory`
- [ ] Add `api_usage` table + migration to persistence.py

### Phase 2: Migrate call sites
- [ ] `poker/controllers.py` - AIPlayerController
- [ ] `poker/poker_player.py` - AIPokerPlayer
- [ ] `poker/memory/commentary_generator.py`
- [ ] `poker/personality_generator.py`
- [ ] `poker/character_images.py`
- [ ] `flask_app/routes/stats_routes.py`
- [ ] `flask_app/routes/personality_routes.py`
- [ ] `core/llm_categorizer.py`
- [ ] `spades/spades_game.py`

### Phase 3: Cleanup
- [ ] Delete `core/assistants.py`
- [ ] Update tests
- [ ] Update CLAUDE.md

---

## Out of Scope (Future)

- Anthropic/Groq providers (add when needed)
- Provider pricing table (calculate costs from tokens later)
- Character system extraction to `core/characters/` (separate effort)
- Cost dashboard/API endpoints

---

## Open Questions

1. ~~Database location~~ → Same DB (`poker_games.db`) for easier joins
2. ~~Backwards compat~~ → Not needed, clean migration
3. Should `UsageTracker` be a singleton or passed explicitly? → Recommend: default singleton with override option
