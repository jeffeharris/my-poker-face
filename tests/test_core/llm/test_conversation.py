"""Tests for ConversationMemory."""
import unittest

from core.llm import ConversationMemory


class TestConversationMemory(unittest.TestCase):
    """Tests for ConversationMemory class."""

    def test_init_default(self):
        """Test default initialization."""
        memory = ConversationMemory()
        self.assertEqual(memory.system_prompt, "")
        self.assertEqual(memory.max_messages, 15)
        self.assertEqual(len(memory), 0)

    def test_init_with_params(self):
        """Test initialization with parameters."""
        memory = ConversationMemory(
            system_prompt="You are a helpful assistant.",
            max_messages=10
        )
        self.assertEqual(memory.system_prompt, "You are a helpful assistant.")
        self.assertEqual(memory.max_messages, 10)

    def test_add_user_message(self):
        """Test adding user messages."""
        memory = ConversationMemory()
        memory.add_user("Hello")
        self.assertEqual(len(memory), 1)
        self.assertEqual(memory.get_history()[0]["role"], "user")
        self.assertEqual(memory.get_history()[0]["content"], "Hello")

    def test_add_assistant_message(self):
        """Test adding assistant messages."""
        memory = ConversationMemory()
        memory.add_assistant("Hi there!")
        self.assertEqual(len(memory), 1)
        self.assertEqual(memory.get_history()[0]["role"], "assistant")

    def test_get_messages_includes_system(self):
        """Test that get_messages includes system prompt."""
        memory = ConversationMemory(system_prompt="System prompt here")
        memory.add_user("User message")

        messages = memory.get_messages()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "System prompt here")
        self.assertEqual(messages[1]["role"], "user")

    def test_get_messages_no_system(self):
        """Test get_messages with no system prompt."""
        memory = ConversationMemory()
        memory.add_user("User message")

        messages = memory.get_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")

    def test_trim_messages(self):
        """Test that messages are trimmed to max_messages."""
        memory = ConversationMemory(max_messages=3)

        for i in range(5):
            memory.add_user(f"Message {i}")

        self.assertEqual(len(memory), 3)
        # Should keep the last 3
        history = memory.get_history()
        self.assertEqual(history[0]["content"], "Message 2")
        self.assertEqual(history[2]["content"], "Message 4")

    def test_clear(self):
        """Test clearing memory."""
        memory = ConversationMemory(system_prompt="Keep this")
        memory.add_user("Message 1")
        memory.add_assistant("Response 1")

        memory.clear()

        self.assertEqual(len(memory), 0)
        # System prompt should still be there
        messages = memory.get_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "Keep this")

    def test_serialization(self):
        """Test to_dict and from_dict."""
        memory = ConversationMemory(system_prompt="Test", max_messages=10)
        memory.add_user("Hello")
        memory.add_assistant("Hi")

        data = memory.to_dict()
        restored = ConversationMemory.from_dict(data)

        self.assertEqual(restored.system_prompt, "Test")
        self.assertEqual(restored.max_messages, 10)
        self.assertEqual(len(restored), 2)
        self.assertEqual(restored.get_history()[0]["content"], "Hello")


if __name__ == "__main__":
    unittest.main()
