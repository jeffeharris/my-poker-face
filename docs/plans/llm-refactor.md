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
│   ├── __init__.py              # Exports: LLMClient, Assistant, LLMResponse, CallType, UsageTracker
│   ├── client.py                # LLMClient - low-level, stateless
│   ├── assistant.py             # Assistant - convenience wrapper with memory
│   ├── response.py              # LLMResponse, ImageResponse dataclasses
│   ├── tracking.py              # UsageTracker - persists to DB, CallType enum
│   ├── conversation.py          # ConversationMemory - state management
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

    -- Context (nullable - not all calls have game context)
    game_id TEXT REFERENCES games(game_id) ON DELETE SET NULL,
    owner_id TEXT,
    player_name TEXT,
    hand_number INTEGER,

    -- Call classification (validated enum in code)
    call_type TEXT NOT NULL,
    prompt_template TEXT,

    -- Provider/Model
    provider TEXT NOT NULL,
    model TEXT NOT NULL,

    -- Token usage (for text completions)
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,

    -- Image usage (for DALL-E - cost is per-image, not tokens)
    image_count INTEGER DEFAULT 0,
    image_size TEXT,              -- '256x256', '512x512', '1024x1024'

    -- Performance & Status
    latency_ms INTEGER,
    status TEXT NOT NULL,         -- 'ok', 'error', 'fallback'
    finish_reason TEXT,
    error_code TEXT,
    fallback_used BOOLEAN DEFAULT FALSE
);

-- Single-column indexes
CREATE INDEX idx_api_usage_owner ON api_usage(owner_id);
CREATE INDEX idx_api_usage_game ON api_usage(game_id);
CREATE INDEX idx_api_usage_created ON api_usage(created_at);
CREATE INDEX idx_api_usage_call_type ON api_usage(call_type);

-- Composite indexes for common cost queries
CREATE INDEX idx_api_usage_owner_created ON api_usage(owner_id, created_at);
CREATE INDEX idx_api_usage_owner_call_type ON api_usage(owner_id, call_type);
CREATE INDEX idx_api_usage_game_call_type ON api_usage(game_id, call_type);
CREATE INDEX idx_api_usage_model_created ON api_usage(model, created_at);
```

---

## Call Types (Enum)

```python
# core/llm/tracking.py
from enum import Enum

class CallType(str, Enum):
    """Validated call types for usage tracking."""
    PLAYER_DECISION = "player_decision"
    COMMENTARY = "commentary"
    CHAT_SUGGESTION = "chat_suggestion"
    TARGETED_CHAT = "targeted_chat"
    PERSONALITY_GENERATION = "personality_generation"
    PERSONALITY_PREVIEW = "personality_preview"
    IMAGE_GENERATION = "image_generation"
    IMAGE_DESCRIPTION = "image_description"
    CATEGORIZATION = "categorization"      # For llm_categorizer
    SPADES_DECISION = "spades_decision"    # For spades game
```

| CallType | Location | Context Available |
|----------|----------|-------------------|
| `PLAYER_DECISION` | `poker/controllers.py` | game_id, player_name, owner_id |
| `COMMENTARY` | `poker/memory/commentary_generator.py` | game_id, player_name |
| `CHAT_SUGGESTION` | `flask_app/routes/stats_routes.py` | game_id, owner_id |
| `TARGETED_CHAT` | `flask_app/routes/stats_routes.py` | game_id, owner_id |
| `PERSONALITY_GENERATION` | `poker/personality_generator.py` | owner_id |
| `PERSONALITY_PREVIEW` | `flask_app/routes/personality_routes.py` | owner_id |
| `IMAGE_GENERATION` | `poker/character_images.py` | (none - one-time) |
| `IMAGE_DESCRIPTION` | `poker/character_images.py` | (none - one-time) |

---

## Key Interfaces

### LLMClient (Low-level, stateless)

```python
class LLMClient:
    """Low-level LLM client. Use Assistant for conversation flows."""

    def __init__(
        self,
        provider: str = "openai",
        model: str = None,
        reasoning_effort: str = "low",  # GPT-5: 'minimal', 'low', 'medium', 'high'
        tracker: UsageTracker = None
    ): ...

    def complete(
        self,
        messages: List[dict],
        json_format: bool = False,
        max_tokens: int = 2800,
        # Tracking context
        call_type: CallType = None,
        game_id: str = None,
        owner_id: str = None,
        player_name: str = None,
        prompt_template: str = None,
    ) -> LLMResponse: ...

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        call_type: CallType = CallType.IMAGE_GENERATION,
        **context
    ) -> ImageResponse: ...
```

### Assistant (Convenience wrapper with memory)

```python
class Assistant:
    """
    High-level assistant with conversation memory.
    Drop-in replacement for OpenAILLMAssistant.chat() pattern.
    """

    def __init__(
        self,
        system_prompt: str = "",
        model: str = None,
        reasoning_effort: str = "low",
        max_memory: int = 15,
        # Default tracking context (can override per-call)
        call_type: CallType = None,
        game_id: str = None,
        owner_id: str = None,
        player_name: str = None,
    ):
        self._client = LLMClient(model=model, reasoning_effort=reasoning_effort)
        self._memory = ConversationMemory(system_prompt=system_prompt, max_messages=max_memory)
        self._default_context = {...}

    def chat(
        self,
        message: str,
        json_format: bool = False,
        # Override default context if needed
        call_type: CallType = None,
        **context_overrides
    ) -> str:
        """
        Send message and get response. Handles memory automatically.
        Returns content string (like old assistant.chat()).
        """
        self._memory.add_user(message)

        response = self._client.complete(
            messages=self._memory.get_messages(),
            json_format=json_format,
            call_type=call_type or self._default_context.get('call_type'),
            **{**self._default_context, **context_overrides}
        )

        self._memory.add_assistant(response.content)
        return response.content

    def chat_full(self, message: str, ...) -> LLMResponse:
        """Like chat() but returns full LLMResponse for access to tokens, etc."""
        ...

    @property
    def memory(self) -> ConversationMemory:
        """Access memory for serialization/inspection."""
        return self._memory

    def reset_memory(self):
        self._memory.clear()
```

### LLMResponse / ImageResponse

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
    status: str = "ok"
    raw_response: Any = None

@dataclass
class ImageResponse:
    url: str                    # Or base64 data
    model: str
    provider: str
    size: str
    latency_ms: float = 0
    status: str = "ok"
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

## Migration Examples

### Before (current)
```python
from core.assistants import OpenAILLMAssistant

assistant = OpenAILLMAssistant(
    ai_model="gpt-5-nano",
    system_message="You are a poker player...",
    ai_temp=0.9  # Ignored for GPT-5
)
response = assistant.chat(prompt, json_format=True)
```

### After (new)
```python
from core.llm import Assistant, CallType

assistant = Assistant(
    model="gpt-5-nano",
    system_prompt="You are a poker player...",
    call_type=CallType.PLAYER_DECISION,
    game_id=game_id,
    player_name=self.player_name
)
response = assistant.chat(prompt, json_format=True)
```

### Stateless call (no memory needed)
```python
from core.llm import LLMClient, CallType

client = LLMClient(model="gpt-5-mini")
response = client.complete(
    messages=[{"role": "user", "content": prompt}],
    json_format=True,
    call_type=CallType.IMAGE_DESCRIPTION
)
```

---

## Testing Strategy

### Unit Tests
- `tests/core/llm/test_client.py` - Mock provider, verify tracking calls
- `tests/core/llm/test_assistant.py` - Verify memory management
- `tests/core/llm/test_tracking.py` - Verify DB persistence
- `tests/core/llm/test_conversation.py` - Memory operations

### Integration Tests
- `tests/core/llm/test_openai_provider.py` - Real API calls (skip in CI, run manually)
- `tests/test_usage_tracking_integration.py` - End-to-end tracking through game flow

### Migration Tests
- Verify each migrated call site still works
- Compare token counts between old and new implementations

---

## Migration Checklist

### Phase 1: Create new infrastructure
- [ ] Create `core/llm/` package structure
- [ ] Implement `CallType` enum
- [ ] Implement `LLMResponse`, `ImageResponse` dataclasses
- [ ] Implement `LLMProvider` ABC and `OpenAIProvider`
- [ ] Implement `LLMClient`
- [ ] Implement `ConversationMemory`
- [ ] Implement `Assistant` convenience wrapper
- [ ] Implement `UsageTracker` (with DB persistence)
- [ ] Add `api_usage` table + migration to persistence.py
- [ ] Add unit tests for new classes

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
- [ ] Update existing tests (fix imports, mocks)
- [ ] Update CLAUDE.md

---

## Image Cost Tracking

DALL-E pricing is per-image, not per-token:
- Track `image_count` and `image_size` in api_usage
- Cost calculation: `image_count * price_per_size[image_size]`
- Current pricing (DALL-E 2): $0.020/image for 1024x1024

---

## Out of Scope (Future)

- Anthropic/Groq providers (add when needed)
- Provider pricing table (calculate costs from stored tokens/images later)
- Character system extraction to `core/characters/` (separate effort)
- Cost dashboard/API endpoints
- Prompt caching optimization

---

## Resolved Questions

1. ~~Database location~~ → Same DB (`poker_games.db`) for easier joins
2. ~~Backwards compat~~ → Not needed, clean migration
3. ~~UsageTracker singleton?~~ → Default singleton with override option for testing
4. ~~Temperature parameter~~ → Removed; only `reasoning_effort` for GPT-5 models
5. ~~Convenience API~~ → Added `Assistant` class with `.chat()` method
