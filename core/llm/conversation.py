"""Conversation memory management."""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class ConversationMemory:
    """Manages conversation history for stateful LLM interactions.

    Keeps track of messages exchanged with an LLM, with automatic
    trimming to stay within a maximum message limit.
    """

    system_prompt: str = ""
    max_messages: int = 15
    _messages: List[Dict[str, str]] = field(default_factory=list)

    def add_user(self, content: str) -> None:
        """Add a user message to memory."""
        self._messages.append({"role": "user", "content": content})
        self._trim()

    def add_assistant(self, content: str) -> None:
        """Add an assistant message to memory."""
        self._messages.append({"role": "assistant", "content": content})
        self._trim()

    def add(self, role: str, content: str) -> None:
        """Add a message with specified role."""
        self._messages.append({"role": role, "content": content})
        self._trim()

    def get_messages(self) -> List[Dict[str, str]]:
        """Get all messages including system prompt, ready for LLM call."""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(self._messages)
        return messages

    def get_history(self) -> List[Dict[str, str]]:
        """Get just the conversation history (without system prompt)."""
        return list(self._messages)

    def clear(self) -> None:
        """Clear all messages from memory."""
        self._messages = []

    def _trim(self) -> None:
        """Trim messages to stay within max_messages limit."""
        if len(self._messages) > self.max_messages:
            self._messages = self._messages[-self.max_messages:]

    def __len__(self) -> int:
        """Return number of messages in memory."""
        return len(self._messages)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize memory to dictionary for persistence."""
        return {
            "system_prompt": self.system_prompt,
            "max_messages": self.max_messages,
            "messages": list(self._messages),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationMemory":
        """Deserialize memory from dictionary."""
        memory = cls(
            system_prompt=data.get("system_prompt", ""),
            max_messages=data.get("max_messages", 15),
        )
        memory._messages = list(data.get("messages", []))
        return memory
