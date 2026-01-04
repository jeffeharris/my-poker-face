"""High-level assistant with conversation memory."""
from typing import Optional, Dict, Any

from .client import LLMClient
from .conversation import ConversationMemory
from .response import LLMResponse
from .tracking import CallType, UsageTracker


class Assistant:
    """High-level assistant with conversation memory.

    Drop-in replacement for OpenAILLMAssistant.chat() pattern.
    Combines LLMClient with ConversationMemory for stateful conversations.

    Example:
        assistant = Assistant(
            system_prompt="You are a poker player...",
            model="gpt-4.1-nano",
            call_type=CallType.PLAYER_DECISION,
            game_id="game_123",
            player_name="Batman"
        )
        response = assistant.chat("What's your move?", json_format=True)
    """

    def __init__(
        self,
        system_prompt: str = "",
        model: Optional[str] = None,
        reasoning_effort: str = "low",
        max_memory: int = 15,
        tracker: Optional[UsageTracker] = None,
        # Default tracking context (can override per-call)
        call_type: Optional[CallType] = None,
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        player_name: Optional[str] = None,
    ):
        """Initialize assistant.

        Args:
            system_prompt: System prompt for the conversation
            model: Model to use (provider default if None)
            reasoning_effort: Reasoning effort for models that support it
            max_memory: Maximum messages to keep in memory
            tracker: Usage tracker (uses default singleton if None)
            call_type: Default call type for tracking
            game_id: Default game ID for tracking
            owner_id: Default user ID for tracking
            player_name: Default AI player name for tracking
        """
        self._client = LLMClient(
            model=model,
            reasoning_effort=reasoning_effort,
            tracker=tracker,
        )
        self._memory = ConversationMemory(
            system_prompt=system_prompt,
            max_messages=max_memory,
        )
        self._default_context = {
            "call_type": call_type,
            "game_id": game_id,
            "owner_id": owner_id,
            "player_name": player_name,
        }

        # Expose for compatibility with existing code
        self.system_message = system_prompt
        self.ai_model = self._client.model

    def chat(
        self,
        message: str,
        json_format: bool = False,
        # Override default context if needed
        call_type: Optional[CallType] = None,
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        player_name: Optional[str] = None,
        hand_number: Optional[int] = None,
        prompt_template: Optional[str] = None,
    ) -> str:
        """Send message and get response. Handles memory automatically.

        Args:
            message: User message to send
            json_format: Whether to request JSON output
            call_type: Override default call type
            game_id: Override default game ID
            owner_id: Override default owner ID
            player_name: Override default player name
            hand_number: Hand number for tracking
            prompt_template: Prompt template name for tracking

        Returns:
            Assistant's response content (string)
        """
        response = self.chat_full(
            message=message,
            json_format=json_format,
            call_type=call_type,
            game_id=game_id,
            owner_id=owner_id,
            player_name=player_name,
            hand_number=hand_number,
            prompt_template=prompt_template,
        )
        return response.content

    def chat_full(
        self,
        message: str,
        json_format: bool = False,
        call_type: Optional[CallType] = None,
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        player_name: Optional[str] = None,
        hand_number: Optional[int] = None,
        prompt_template: Optional[str] = None,
    ) -> LLMResponse:
        """Like chat() but returns full LLMResponse for access to tokens, etc.

        Args:
            message: User message to send
            json_format: Whether to request JSON output
            call_type: Override default call type
            game_id: Override default game ID
            owner_id: Override default owner ID
            player_name: Override default player name
            hand_number: Hand number for tracking
            prompt_template: Prompt template name for tracking

        Returns:
            Full LLMResponse object
        """
        # Add user message to memory
        self._memory.add_user(message)

        # Merge context: explicit params override defaults
        context = {
            "call_type": call_type or self._default_context["call_type"],
            "game_id": game_id or self._default_context["game_id"],
            "owner_id": owner_id or self._default_context["owner_id"],
            "player_name": player_name or self._default_context["player_name"],
            "hand_number": hand_number,
            "prompt_template": prompt_template,
        }

        # Make LLM call
        response = self._client.complete(
            messages=self._memory.get_messages(),
            json_format=json_format,
            **context,
        )

        # Add assistant response to memory
        if response.content:
            self._memory.add_assistant(response.content)

        return response

    @property
    def memory(self) -> ConversationMemory:
        """Access memory for serialization/inspection."""
        return self._memory

    def reset_memory(self) -> None:
        """Clear conversation memory."""
        self._memory.clear()

    def add_to_memory(self, message: Dict[str, str]) -> None:
        """Add a message to memory (for compatibility).

        Args:
            message: Dict with 'role' and 'content' keys
        """
        self._memory.add(message["role"], message["content"])

    def to_dict(self) -> Dict[str, Any]:
        """Serialize assistant state for persistence."""
        return {
            "system_prompt": self._memory.system_prompt,
            "model": self._client.model,
            "memory": self._memory.to_dict(),
            "default_context": self._default_context,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], tracker: Optional[UsageTracker] = None) -> "Assistant":
        """Deserialize assistant from dictionary."""
        assistant = cls(
            system_prompt=data.get("system_prompt", ""),
            model=data.get("model"),
            tracker=tracker,
            **data.get("default_context", {}),
        )
        if "memory" in data:
            assistant._memory = ConversationMemory.from_dict(data["memory"])
        return assistant
