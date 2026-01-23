"""Tests for Assistant class."""
import unittest
from unittest.mock import Mock, patch
import tempfile
import os

from core.llm import Assistant, CallType, UsageTracker
from core.llm.response import LLMResponse


class TestAssistant(unittest.TestCase):
    """Tests for Assistant class."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a temp database for tracking
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()

        # Initialize persistence to create tables
        from poker.persistence import GamePersistence
        GamePersistence(self.temp_db.name)

        self.tracker = UsageTracker(db_path=self.temp_db.name)

    def tearDown(self):
        """Clean up temp files."""
        os.unlink(self.temp_db.name)

    @patch('core.llm.providers.openai.OpenAI')
    def test_init_defaults(self, mock_openai_class):
        """Test Assistant initialization with defaults."""
        assistant = Assistant(tracker=self.tracker)

        self.assertEqual(assistant.system_message, "")
        self.assertIsNotNone(assistant.ai_model)
        # Empty system prompt means no system message in memory
        self.assertEqual(len(assistant.memory.get_messages()), 0)

    @patch('core.llm.providers.openai.OpenAI')
    def test_init_with_system_prompt(self, mock_openai_class):
        """Test Assistant initialization with system prompt."""
        prompt = "You are a poker player."
        assistant = Assistant(system_prompt=prompt, tracker=self.tracker)

        self.assertEqual(assistant.system_message, prompt)
        messages = assistant.memory.get_messages()
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], prompt)

    @patch('core.llm.providers.openai.OpenAI')
    def test_init_with_tracking_context(self, mock_openai_class):
        """Test Assistant initialization with tracking context."""
        assistant = Assistant(
            system_prompt="Test",
            call_type=CallType.PLAYER_DECISION,
            game_id="game_123",
            owner_id="user_456",
            player_name="Batman",
            tracker=self.tracker,
        )

        # Verify context is stored
        self.assertEqual(assistant._default_context["call_type"], CallType.PLAYER_DECISION)
        self.assertEqual(assistant._default_context["game_id"], "game_123")
        self.assertEqual(assistant._default_context["owner_id"], "user_456")
        self.assertEqual(assistant._default_context["player_name"], "Batman")

    @patch('core.llm.providers.openai.OpenAI')
    def test_chat_returns_string(self, mock_openai_class):
        """Test chat() returns string content."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "I fold."
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.completion_tokens_details = None
        mock_response.usage.prompt_tokens_details = None

        mock_client.chat.completions.create.return_value = mock_response

        assistant = Assistant(
            system_prompt="You are a poker player.",
            tracker=self.tracker,
        )
        response = assistant.chat("What's your move?")

        self.assertEqual(response, "I fold.")
        self.assertIsInstance(response, str)

    @patch('core.llm.providers.openai.OpenAI')
    def test_chat_full_returns_llm_response(self, mock_openai_class):
        """Test chat_full() returns LLMResponse."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "I fold."
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.completion_tokens_details = None
        mock_response.usage.prompt_tokens_details = None

        mock_client.chat.completions.create.return_value = mock_response

        assistant = Assistant(
            system_prompt="You are a poker player.",
            tracker=self.tracker,
        )
        response = assistant.chat_full("What's your move?")

        self.assertIsInstance(response, LLMResponse)
        self.assertEqual(response.content, "I fold.")
        self.assertEqual(response.input_tokens, 10)
        self.assertEqual(response.output_tokens, 5)

    @patch('core.llm.providers.openai.OpenAI')
    def test_chat_adds_to_memory(self, mock_openai_class):
        """Test that chat adds messages to memory."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.completion_tokens_details = None
        mock_response.usage.prompt_tokens_details = None

        mock_client.chat.completions.create.return_value = mock_response

        assistant = Assistant(
            system_prompt="Test",
            tracker=self.tracker,
        )

        # Initially just system message
        self.assertEqual(len(assistant.memory.get_messages()), 1)

        # After chat, should have system + user + assistant
        assistant.chat("Hi there")
        messages = assistant.memory.get_messages()
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "Hi there")
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["content"], "Hello!")

    @patch('core.llm.providers.openai.OpenAI')
    def test_chat_context_override(self, mock_openai_class):
        """Test that per-call context overrides defaults."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "OK"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.completion_tokens_details = None
        mock_response.usage.prompt_tokens_details = None

        mock_client.chat.completions.create.return_value = mock_response

        assistant = Assistant(
            system_prompt="Test",
            call_type=CallType.PLAYER_DECISION,
            game_id="game_default",
            tracker=self.tracker,
        )

        # Override game_id per-call
        assistant.chat(
            "Test message",
            game_id="game_override",
        )

        # Check that override was used
        import sqlite3
        with sqlite3.connect(self.temp_db.name) as conn:
            cursor = conn.execute("SELECT game_id FROM api_usage")
            row = cursor.fetchone()
            self.assertEqual(row[0], "game_override")

    @patch('core.llm.providers.openai.OpenAI')
    def test_memory_clear(self, mock_openai_class):
        """Test memory.clear() clears conversation."""
        assistant = Assistant(
            system_prompt="Test",
            tracker=self.tracker,
        )

        # Add some messages manually
        assistant._memory.add_user("Hello")
        assistant._memory.add_assistant("Hi there!")
        self.assertEqual(len(assistant.memory.get_messages()), 3)

        # Clear via memory property
        assistant.memory.clear()

        # Should only have system message
        messages = assistant.memory.get_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "system")

    @patch('core.llm.providers.openai.OpenAI')
    def test_memory_add_messages(self, mock_openai_class):
        """Test adding messages via memory property."""
        assistant = Assistant(
            system_prompt="Test",
            tracker=self.tracker,
        )

        assistant.memory.add("user", "Custom message")

        messages = assistant.memory.get_messages()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["content"], "Custom message")

    @patch('core.llm.providers.openai.OpenAI')
    def test_to_dict(self, mock_openai_class):
        """Test serialization to dict."""
        assistant = Assistant(
            system_prompt="You are helpful.",
            call_type=CallType.COMMENTARY,
            game_id="game_123",
            tracker=self.tracker,
        )

        data = assistant.to_dict()

        self.assertEqual(data["system_prompt"], "You are helpful.")
        self.assertIsNotNone(data["model"])
        self.assertIn("memory", data)
        self.assertIn("default_context", data)
        self.assertEqual(data["default_context"]["call_type"], CallType.COMMENTARY)
        self.assertEqual(data["default_context"]["game_id"], "game_123")

    @patch('core.llm.providers.openai.OpenAI')
    def test_from_dict(self, mock_openai_class):
        """Test deserialization from dict."""
        data = {
            "system_prompt": "Restored prompt",
            "model": "gpt-5-nano",
            "memory": {
                "system_prompt": "Restored prompt",
                "messages": [
                    {"role": "user", "content": "Previous message"},
                    {"role": "assistant", "content": "Previous response"},
                ]
            },
            "default_context": {
                "call_type": CallType.PLAYER_DECISION,
                "game_id": "restored_game",
                "owner_id": None,
                "player_name": "Joker",
            }
        }

        assistant = Assistant.from_dict(data, tracker=self.tracker)

        self.assertEqual(assistant.system_message, "Restored prompt")
        self.assertEqual(assistant._default_context["game_id"], "restored_game")
        self.assertEqual(assistant._default_context["player_name"], "Joker")

        # Check memory was restored
        messages = assistant.memory.get_messages()
        self.assertEqual(len(messages), 3)  # system + 2 restored
        self.assertEqual(messages[1]["content"], "Previous message")

    @patch('core.llm.providers.openai.OpenAI')
    def test_roundtrip_serialization(self, mock_openai_class):
        """Test that to_dict/from_dict preserves state."""
        original = Assistant(
            system_prompt="Test prompt",
            call_type=CallType.SPADES_DECISION,
            game_id="test_game",
            player_name="TestPlayer",
            tracker=self.tracker,
        )

        # Add some memory
        original._memory.add_user("Message 1")
        original._memory.add_assistant("Response 1")

        # Serialize and deserialize
        data = original.to_dict()
        restored = Assistant.from_dict(data, tracker=self.tracker)

        self.assertEqual(restored.system_message, "Test prompt")
        self.assertEqual(
            len(restored.memory.get_messages()),
            len(original.memory.get_messages())
        )

    @patch('core.llm.providers.openai.OpenAI')
    def test_json_format(self, mock_openai_class):
        """Test chat with JSON format."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = '{"action": "raise", "amount": 100}'
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.completion_tokens_details = None
        mock_response.usage.prompt_tokens_details = None

        mock_client.chat.completions.create.return_value = mock_response

        assistant = Assistant(
            system_prompt="Return JSON.",
            tracker=self.tracker,
        )
        response = assistant.chat("What's your move?", json_format=True)

        # Verify JSON format was requested
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(response, '{"action": "raise", "amount": 100}')


if __name__ == "__main__":
    unittest.main()
