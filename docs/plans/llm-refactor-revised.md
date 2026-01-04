# LLM Refactor & Cost Tracking Plan (REVISED)

**Status**: Ready for Implementation  
**Goal**: Unified LLM abstraction with built-in cost tracking, replacing `core/assistants.py`  
**Previous Version**: `llm-refactor.md`  
**Review Document**: `llm-refactor-review.md`

---

## Overview

Replace the legacy `OpenAILLMAssistant` with a clean architecture that:
1. Separates LLM calls from conversation memory
2. Tracks all API usage with context (game, user, call type)
3. Supports multiple providers (OpenAI now, Anthropic/Groq later)
4. **NEW**: Provides convenient high-level API alongside low-level control
5. **NEW**: Clean break - no backwards compatibility needed (breaking changes acceptable)

---

## File Structure

```
core/
├── llm/
│   ├── __init__.py              # Exports: LLMClient, LLMResponse, UsageTracker, ConversationMemory
│   ├── client.py                # LLMClient - unified entry point
│   ├── response.py              # LLMResponse, ImageResponse dataclasses
│   ├── tracking.py              # UsageTracker (singleton), LLMCallType enum
│   ├── conversation.py          # ConversationMemory - stateful wrapper
│   └── providers/
│       ├── __init__.py
│       ├── base.py              # LLMProvider ABC
│       └── openai.py            # OpenAIProvider implementation
│
├── assistants.py                # DELETE immediately after migration
└── llm_categorizer.py           # UPDATE to use LLMClient
```

---

## Database Schema (REVISED)

Add to `poker/persistence.py`:

```sql
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Context
    game_id TEXT,
    owner_id TEXT,
    player_name TEXT,
    hand_number INTEGER,
    call_type TEXT NOT NULL,                -- Required, validated via enum
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

    -- Request parameters
    json_format BOOLEAN DEFAULT FALSE,
    temperature REAL,
    reasoning_effort TEXT,                   -- For GPT-5 models

    -- Performance & Status
    latency_ms INTEGER,
    status TEXT NOT NULL,                    -- 'ok', 'error', 'fallback'
    finish_reason TEXT,
    error_code TEXT,
    error_message TEXT,
    fallback_used BOOLEAN DEFAULT FALSE,
    retry_count INTEGER DEFAULT 0,

    -- Foreign key with soft delete (preserve cost data)
    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE SET NULL
);

CREATE INDEX idx_api_usage_owner ON api_usage(owner_id);
CREATE INDEX idx_api_usage_game ON api_usage(game_id);
CREATE INDEX idx_api_usage_created ON api_usage(created_at DESC);
CREATE INDEX idx_api_usage_call_type ON api_usage(call_type);
CREATE INDEX idx_api_usage_provider_model ON api_usage(provider, model, created_at DESC);
CREATE INDEX idx_api_usage_status ON api_usage(status);
```

### Schema Version Bump

Add migration to schema version tracking:

```python
# In poker/persistence.py
SCHEMA_VERSION = 5  # Increment from current

def migrate_to_version_5(conn):
    """Add api_usage table. Breaking change: delete incompatible saved games."""
    conn.execute(CREATE_API_USAGE_TABLE)
    conn.execute(CREATE_INDICES)
    
    # Clean slate: delete all games (breaking change - acceptable in dev)
    conn.execute("DELETE FROM games")
    conn.execute("DELETE FROM ai_player_state")
    conn.execute("DELETE FROM personality_snapshots")
    conn.execute("DELETE FROM pressure_events")
```

---

## Key Interfaces (REVISED)

### LLMClient

```python
from typing import List, Dict, Optional, Any
from .conversation import ConversationMemory
from .tracking import UsageTracker, LLMCallType

class LLMClient:
    """
    Unified LLM client with usage tracking.
    
    Supports two usage patterns:
    1. Low-level: complete(messages=[...]) for full control
    2. High-level: chat(text) with automatic memory management
    """
    
    def __init__(
        self,
        provider: str = "openai",
        model: str = None,
        tracker: UsageTracker = None,
        conversation: ConversationMemory = None,
        default_context: Dict[str, Any] = None
    ):
        """
        Args:
            provider: LLM provider ("openai", "anthropic", etc.)
            model: Model name (defaults to provider default)
            tracker: UsageTracker instance (defaults to singleton)
            conversation: Optional ConversationMemory for chat() method
            default_context: Default tracking context (game_id, player_name, etc.)
        """
        self.provider = self._get_provider(provider, model)
        self.tracker = tracker or UsageTracker.get_instance()
        self.conversation = conversation
        self.default_context = default_context or {}
    
    # LOW-LEVEL API - Full control
    def complete(
        self,
        messages: List[Dict[str, str]],
        json_format: bool = False,
        temperature: float = 1.0,
        max_tokens: int = 2800,
        # Tracking context
        call_type: str = None,
        game_id: str = None,
        owner_id: str = None,
        player_name: str = None,
        prompt_template: str = None,
        **kwargs
    ) -> LLMResponse:
        """
        Low-level completion API - full control over messages.
        Does NOT modify conversation memory.
        """
        # Merge with default context
        context = {**self.default_context}
        if game_id: context['game_id'] = game_id
        if owner_id: context['owner_id'] = owner_id
        if player_name: context['player_name'] = player_name
        if call_type: context['call_type'] = call_type
        if prompt_template: context['prompt_template'] = prompt_template
        
        # Validate call_type
        if context.get('call_type'):
            context['call_type'] = LLMCallType.validate(context['call_type'])
        
        # Call provider
        response = self.provider.complete(
            messages=messages,
            json_format=json_format,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )
        
        # Track usage
        self.tracker.record(response, context)
        
        return response
    
    # HIGH-LEVEL API - Convenience
    def chat(
        self,
        user_content: str,
        json_format: bool = False,
        **tracking_context
    ) -> LLMResponse:
        """
        High-level chat API - automatic memory management.
        Requires conversation to be set in __init__.
        
        Automatically:
        - Adds user message to conversation
        - Calls complete() with conversation history
        - Adds assistant response to conversation
        
        Args:
            user_content: User message text
            json_format: Request JSON response format
            **tracking_context: Override default tracking context
        
        Returns:
            LLMResponse
        
        Raises:
            ValueError: If conversation is None
        """
        if self.conversation is None:
            raise ValueError("chat() requires conversation to be set in __init__")
        
        # Add user message
        self.conversation.add_user(user_content)
        
        # Get response
        context = {**self.default_context, **tracking_context}
        response = self.complete(
            messages=self.conversation.get_messages(),
            json_format=json_format,
            **context
        )
        
        # Add assistant response
        self.conversation.add_assistant(response.content)
        
        return response
    
    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = "dall-e-2",
        call_type: str = None,
        **context
    ) -> ImageResponse:
        """
        Generate image using DALL-E.
        
        Cost tracking for images:
        - input_tokens = 0, output_tokens = 0
        - image_count = number of images generated
        - image_size = size parameter
        - Calculate cost as: DALL_E_PRICING[model][size] * image_count
        """
        # Implementation...
        pass
```

### LLMResponse (REVISED)

```python
from dataclasses import dataclass
from typing import Any, Optional

@dataclass
class LLMResponse:
    """Response from LLM completion."""
    content: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: float = 0
    finish_reason: str = ""
    json_format: bool = False
    temperature: Optional[float] = None
    raw_response: Any = None

@dataclass
class ImageResponse:
    """Response from image generation."""
    url: str
    model: str
    provider: str
    size: str
    image_count: int = 1
    latency_ms: float = 0
    cost_estimate: float = 0.0
    retry_count: int = 0
    content_policy_violation: bool = False
    raw_response: Any = None
```

### ConversationMemory

```python
from typing import List, Dict
from dataclasses import dataclass, field

@dataclass
class ConversationMemory:
    """
    Manages conversation history with automatic trimming.
    Simple, clean implementation - no backwards compatibility needed.
    """
    system_prompt: str = ""
    max_messages: int = 15
    messages: List[Dict[str, str]] = field(default_factory=list)
    
    def add_user(self, content: str):
        """Add user message and trim if needed."""
        self.messages.append({"role": "user", "content": content})
        self._trim()
    
    def add_assistant(self, content: str):
        """Add assistant message and trim if needed."""
        self.messages.append({"role": "assistant", "content": content})
        self._trim()
    
    def get_messages(self) -> List[Dict[str, str]]:
        """Get all messages including system prompt."""
        if self.system_prompt:
            return [
                {"role": "system", "content": self.system_prompt},
                *self.messages
            ]
        return self.messages.copy()
    
    def clear(self):
        """Clear all messages (keeps system prompt)."""
        self.messages.clear()
    
    def _trim(self):
        """Keep only last N messages."""
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
    
    def to_dict(self) -> dict:
        """Serialize for database storage."""
        return {
            "system_prompt": self.system_prompt,
            "max_messages": self.max_messages,
            "messages": self.messages
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ConversationMemory":
        """Deserialize from database."""
        return cls(
            system_prompt=data.get("system_prompt", ""),
            max_messages=data.get("max_messages", 15),
            messages=data.get("messages", [])
        )
```
```

### LLMCallType Enum (NEW)

```python
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class LLMCallType(Enum):
    """Enumeration of all LLM call types for tracking."""
    
    # Poker gameplay
    PLAYER_DECISION = "player_decision"
    COMMENTARY = "commentary"
    CHAT_SUGGESTION = "chat_suggestion"
    TARGETED_CHAT = "targeted_chat"
    
    # Personality system
    PERSONALITY_GENERATION = "personality_generation"
    PERSONALITY_PREVIEW = "personality_preview"
    
    # Image generation
    IMAGE_GENERATION = "image_generation"
    IMAGE_DESCRIPTION = "image_description"
    
    # Spades gameplay
    SPADES_BID = "spades_bid"
    SPADES_PLAY_CARD = "spades_play_card"
    SPADES_STRATEGY = "spades_strategy"
    
    # Other
    OTHER = "other"
    
    @classmethod
    def validate(cls, value: str) -> str:
        """
        Validate and return call type value.
        Logs warning for unknown types but allows them.
        """
        if not value:
            return cls.OTHER.value
        
        try:
            # Try to match enum
            return cls(value).value
        except ValueError:
            # Unknown type - log but allow
            logger.warning(f"Unknown call_type: {value}, using as-is")
            return value
```

### UsageTracker (REVISED)

```python
import threading
import logging
from typing import Optional, Dict, Any
from .response import LLMResponse, ImageResponse

logger = logging.getLogger(__name__)

class UsageTracker:
    """
    Tracks LLM API usage to database.
    
    Singleton by default, but can be overridden for testing.
    Thread-safe.
    """
    
    _instance: Optional['UsageTracker'] = None
    _lock = threading.Lock()
    
    def __init__(self, db_path: str = None):
        """
        Args:
            db_path: Path to SQLite database (defaults to poker_games.db)
        """
        from poker.persistence import get_db_connection
        self.get_db_connection = get_db_connection
        self.db_path = db_path
    
    @classmethod
    def get_instance(cls) -> 'UsageTracker':
        """Get singleton instance (lazy initialization)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    @classmethod
    def set_instance(cls, tracker: 'UsageTracker'):
        """Override singleton (for testing)."""
        with cls._lock:
            cls._instance = tracker
    
    def record(self, response: LLMResponse | ImageResponse, context: Dict[str, Any]):
        """
        Record API usage to database.
        
        Args:
            response: LLMResponse or ImageResponse
            context: Dict with tracking context (game_id, call_type, etc.)
        """
        try:
            conn = self.get_db_connection()
            
            if isinstance(response, ImageResponse):
                # Image generation
                conn.execute("""
                    INSERT INTO api_usage (
                        game_id, owner_id, player_name, call_type, prompt_template,
                        provider, model,
                        input_tokens, output_tokens, image_count, image_size,
                        latency_ms, status, retry_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?)
                """, (
                    context.get('game_id'),
                    context.get('owner_id'),
                    context.get('player_name'),
                    context.get('call_type'),
                    context.get('prompt_template'),
                    response.provider,
                    response.model,
                    response.image_count,
                    response.size,
                    response.latency_ms,
                    'ok' if response.url else 'error',
                    response.retry_count
                ))
            else:
                # Text completion
                conn.execute("""
                    INSERT INTO api_usage (
                        game_id, owner_id, player_name, hand_number, call_type, prompt_template,
                        provider, model,
                        input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                        json_format, temperature,
                        latency_ms, status, finish_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    context.get('game_id'),
                    context.get('owner_id'),
                    context.get('player_name'),
                    context.get('hand_number'),
                    context.get('call_type'),
                    context.get('prompt_template'),
                    response.provider,
                    response.model,
                    response.input_tokens,
                    response.output_tokens,
                    response.cached_tokens,
                    response.reasoning_tokens,
                    response.json_format,
                    response.temperature,
                    response.latency_ms,
                    'ok' if response.content else 'error',
                    response.finish_reason
                ))
            
            conn.commit()
            
        except Exception as e:
            logger.error(f"Failed to record API usage: {e}")
            # Don't raise - tracking failures shouldn't break gameplay
```

---

## OpenAI Provider (REVISED)

```python
from typing import List, Dict, Any
import time
import os
from openai import OpenAI
from .base import LLMProvider
from ..response import LLMResponse

class OpenAIProvider(LLMProvider):
    """OpenAI implementation of LLMProvider."""
    
    # Default models
    DEFAULT_MODEL = "gpt-5-nano"
    DEFAULT_FAST_MODEL = "gpt-5-nano"
    
    def __init__(self, model: str = None, api_key: str = None):
        self.model = model or os.environ.get("OPENAI_MODEL", self.DEFAULT_MODEL)
        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
    
    def complete(
        self,
        messages: List[Dict[str, str]],
        json_format: bool = False,
        temperature: float = 1.0,
        max_tokens: int = 2800,
        **kwargs
    ) -> LLMResponse:
        """Make completion request to OpenAI."""
        start_time = time.time()
        
        # Build request kwargs based on model type
        request_kwargs = self._build_kwargs(
            messages=messages,
            json_format=json_format,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )
        
        try:
            response = self.client.chat.completions.create(**request_kwargs)
            latency_ms = (time.time() - start_time) * 1000
            
            usage = response.usage
            content = response.choices[0].message.content or ""
            finish_reason = response.choices[0].finish_reason
            
            # Extract reasoning tokens (GPT-5 models)
            reasoning_tokens = 0
            output_tokens = usage.completion_tokens
            if hasattr(usage, 'completion_tokens_details') and usage.completion_tokens_details:
                reasoning_tokens = getattr(usage.completion_tokens_details, 'reasoning_tokens', 0) or 0
                output_tokens = usage.completion_tokens - reasoning_tokens
            
            return LLMResponse(
                content=content,
                model=self.model,
                provider="openai",
                input_tokens=usage.prompt_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                cached_tokens=getattr(usage, 'cached_tokens', 0),
                latency_ms=latency_ms,
                finish_reason=finish_reason,
                json_format=json_format,
                temperature=temperature if not self._is_gpt5(self.model) else None,
                raw_response=response
            )
            
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            raise  # Re-raise for caller to handle
    
    def _build_kwargs(
        self,
        messages: List[Dict[str, str]],
        json_format: bool,
        temperature: float,
        max_tokens: int,
        **extra_kwargs
    ) -> Dict[str, Any]:
        """Build request kwargs based on model type."""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        
        if json_format:
            kwargs["response_format"] = {"type": "json_object"}
        
        # GPT-5 models use reasoning_effort instead of temperature
        if self._is_gpt5(self.model):
            # Map temperature to reasoning effort
            effort = extra_kwargs.get('reasoning_effort')
            if not effort:
                effort = self._temperature_to_reasoning_effort(temperature)
            kwargs["reasoning_effort"] = effort
        else:
            # GPT-4 and earlier
            kwargs["temperature"] = temperature
            kwargs["top_p"] = extra_kwargs.get('top_p', 1)
            kwargs["frequency_penalty"] = extra_kwargs.get('frequency_penalty', 0)
            kwargs["presence_penalty"] = extra_kwargs.get('presence_penalty', 0)
        
        return kwargs
    
    @staticmethod
    def _is_gpt5(model: str) -> bool:
        """Check if model is GPT-5 series."""
        return model.startswith("gpt-5")
    
    @staticmethod
    def _temperature_to_reasoning_effort(temperature: float) -> str:
        """Map temperature to reasoning effort for GPT-5 models."""
        if temperature < 0.3:
            return "minimal"
        elif temperature < 0.7:
            return "low"
        elif temperature < 1.2:
            return "medium"
        else:
            return "high"
```

---

## Migration Checklist (SIMPLIFIED)

### Phase 0: Preparation
- [ ] Review implementation plan
- [ ] Set up integration test harness
- [ ] **Clean slate**: Delete all existing saved games (breaking change - acceptable)

### Phase 1: Create new infrastructure
- [ ] Create `core/llm/` package structure
- [ ] Implement `LLMResponse` and `ImageResponse` dataclasses
- [ ] Implement `LLMProvider` ABC and `OpenAIProvider`
- [ ] Implement `LLMCallType` enum with validation
- [ ] Implement `LLMClient` (both `complete()` and `chat()` methods)
- [ ] Implement `ConversationMemory` (simple, clean implementation)
- [ ] Implement `UsageTracker` (singleton with override)
- [ ] Add `api_usage` table + schema migration to persistence.py
- [ ] Write unit tests for all new components

### Phase 2: Migrate call sites
- [ ] **Pilot**: `poker/controllers.py` - AIPlayerController
  - [ ] Update to use `LLMClient.chat()`
  - [ ] Add integration test
  - [ ] Manual testing with real game
  - [ ] Verify cost tracking in database
  
- [ ] `poker/poker_player.py` - AIPokerPlayer
  - [ ] Update `to_dict()`/`from_dict()` to new format
  - [ ] No backwards compatibility needed
  
- [ ] `poker/memory/commentary_generator.py`
- [ ] `poker/personality_generator.py`
- [ ] `flask_app/routes/stats_routes.py`
- [ ] `flask_app/routes/personality_routes.py`
- [ ] `core/llm_categorizer.py`
- [ ] `poker/character_images.py` - image generation

**Note**: Spades game migration is out of scope for this refactor.

### Phase 3: Cleanup
- [ ] **Delete** `core/assistants.py` (no legacy code kept)
- [ ] Update all tests to use new LLMClient
- [ ] Update CLAUDE.md and copilot-instructions.md
- [ ] Add cost tracking dashboard/queries

---

## Testing Strategy

### Unit Tests
```python
# tests/core/test_llm_client.py
class TestLLMClient(unittest.TestCase):
    def test_complete_tracks_usage(self):
        # Mock provider and tracker
        # Verify tracker.record() called with correct context
        
    def test_chat_updates_memory(self):
        # Verify conversation memory is updated
        
    def test_json_format_parameter(self):
        # Verify json_format passed to provider

# tests/core/test_conversation_memory.py
class TestConversationMemory(unittest.TestCase):
    def test_trimming(self):
        # Verify max_messages enforcement
        
    def test_serialization(self):
        # Verify to_dict() / from_dict() round-trip
```

### Integration Tests
```python
# tests/test_llm_migration.py
class TestLLMMigration(unittest.TestCase):
    @patch('core.llm.providers.openai.OpenAI')
    def test_ai_player_decision_with_new_client(self, mock_openai):
        # Create game with AI player
        # Mock OpenAI response
        # Verify decision works
        # Check api_usage table
    
    def test_new_game_flow(self):
        # Create new game
        # Play through hand
        # Save and reload
        # Verify new format works
```

### Manual Testing Checklist
- [ ] Play full game with AI players using new client
- [ ] Save and resume game mid-hand
- [ ] Generate personality images
- [ ] Check cost tracking in database
- [ ] Verify chat suggestions work

---

---

## Data Retention Policy

```sql
-- Delete old api_usage records (keep 90 days)
DELETE FROM api_usage 
WHERE created_at < datetime('now', '-90 days');

-- Archive to separate table for long-term analysis
CREATE TABLE api_usage_archive AS 
SELECT * FROM api_usage 
WHERE created_at < datetime('now', '-90 days');

-- Set up scheduled cleanup job (cron or similar)
```

---

## Out of Scope

- Anthropic/Groq providers (add when needed)
- Provider pricing table (calculate costs from tokens later)
- Character system extraction to `core/characters/` (separate effort)
- Cost dashboard/API endpoints (can add after migration)
- Streaming responses
- Function calling support
- **Spades game migration** - not needed for this refactor

---

## Open Questions (RESOLVED)

1. ~~Database location~~ → Same DB (`poker_games.db`) for easier joins ✅
2. ~~Backwards compat~~ → **NO - not needed**, breaking changes acceptable ✅
3. ~~Singleton pattern~~ → Default singleton with override option for testing ✅
4. How to handle rate limiting? → Keep at Flask layer, not in LLMClient ✅
5. Data retention? → 90 days, with archival option ✅

---

## Changes from Original Plan

### Added
- ✅ `LLMClient.chat()` convenience method
- ✅ `LLMCallType` enum for validation
- ✅ Foreign key constraints on `api_usage` table
- ✅ Additional tracking fields (json_format, temperature, retry_count)
- ✅ Testing strategy section
- ✅ Data retention policy
- ✅ GPT-5 parameter handling documentation
- ✅ Composite index for provider+model queries

### Changed
- 🔄 **Clean break** - no backwards compatibility, delete old saved games
- 🔄 Simplified migration - no feature flags or legacy code
- 🔄 `to_dict()`/`from_dict()` use new format only

### Removed
- ❌ Backwards compatibility layer - not needed (breaking changes acceptable)
- ❌ Feature flags and rollback mechanisms
- ❌ Legacy code support (`assistants_legacy.py`)
- ❌ Spades migration - out of scope

---

## Success Criteria

The migration is successful when:
1. ✅ All poker call sites migrated to new LLMClient
2. ✅ All API calls tracked in `api_usage` table
3. ✅ Cost queries work (e.g., "total cost this month")
4. ✅ No increase in error rates
5. ✅ Can generate cost reports by game, player, call type
6. ✅ `core/assistants.py` completely deleted
7. ✅ Clean, maintainable codebase without bloat

---

## Timeline Estimate

- **Phase 0**: 1 day (planning and setup)
- **Phase 1**: 3-4 days (infrastructure)
- **Phase 2**: 5-7 days (migration)
- **Phase 3**: 1 day (cleanup)

**Total**: ~2 weeks of development
