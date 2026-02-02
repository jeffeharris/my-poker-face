"""Tests for Assistant class."""
import sqlite3

from unittest.mock import Mock, patch

from core.llm import Assistant, CallType, UsageTracker
from core.llm.response import LLMResponse


class TestAssistant:
    """Tests for Assistant class."""

    @patch('core.llm.providers.openai.OpenAI')
    def test_init_defaults(self, mock_openai_class, usage_tracker):
        """Test Assistant initialization with defaults."""
        assistant = Assistant(tracker=usage_tracker)

        assert assistant.system_message == ""
        assert assistant.ai_model is not None
        # Empty system prompt means no system message in memory
        assert len(assistant.memory.get_messages()) == 0

    @patch('core.llm.providers.openai.OpenAI')
    def test_init_with_system_prompt(self, mock_openai_class, usage_tracker):
        """Test Assistant initialization with system prompt."""
        prompt = "You are a poker player."
        assistant = Assistant(system_prompt=prompt, tracker=usage_tracker)

        assert assistant.system_message == prompt
        messages = assistant.memory.get_messages()
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == prompt

    @patch('core.llm.providers.openai.OpenAI')
    def test_init_with_tracking_context(self, mock_openai_class, usage_tracker):
        """Test Assistant initialization with tracking context."""
        assistant = Assistant(
            system_prompt="Test",
            call_type=CallType.PLAYER_DECISION,
            game_id="game_123",
            owner_id="user_456",
            player_name="Batman",
            tracker=usage_tracker,
        )

        # Verify context is stored
        assert assistant._default_context["call_type"] == CallType.PLAYER_DECISION
        assert assistant._default_context["game_id"] == "game_123"
        assert assistant._default_context["owner_id"] == "user_456"
        assert assistant._default_context["player_name"] == "Batman"

    @patch('core.llm.providers.openai.OpenAI')
    def test_chat_returns_string(self, mock_openai_class, usage_tracker):
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
            tracker=usage_tracker,
        )
        response = assistant.chat("What's your move?")

        assert response == "I fold."
        assert isinstance(response, str)

    @patch('core.llm.providers.openai.OpenAI')
    def test_chat_full_returns_llm_response(self, mock_openai_class, usage_tracker):
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
            tracker=usage_tracker,
        )
        response = assistant.chat_full("What's your move?")

        assert isinstance(response, LLMResponse)
        assert response.content == "I fold."
        assert response.input_tokens == 10
        assert response.output_tokens == 5

    @patch('core.llm.providers.openai.OpenAI')
    def test_chat_adds_to_memory(self, mock_openai_class, usage_tracker):
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
            tracker=usage_tracker,
        )

        # Initially just system message
        assert len(assistant.memory.get_messages()) == 1

        # After chat, should have system + user + assistant
        assistant.chat("Hi there")
        messages = assistant.memory.get_messages()
        assert len(messages) == 3
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hi there"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["content"] == "Hello!"

    @patch('core.llm.providers.openai.OpenAI')
    def test_chat_context_override(self, mock_openai_class, usage_tracker, db_path):
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
            tracker=usage_tracker,
        )

        # Override game_id per-call
        assistant.chat(
            "Test message",
            game_id="game_override",
        )

        # Check that override was used
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("SELECT game_id FROM api_usage")
            row = cursor.fetchone()
            assert row[0] == "game_override"

    @patch('core.llm.providers.openai.OpenAI')
    def test_memory_clear(self, mock_openai_class, usage_tracker):
        """Test memory.clear() clears conversation."""
        assistant = Assistant(
            system_prompt="Test",
            tracker=usage_tracker,
        )

        # Add some messages manually
        assistant._memory.add_user("Hello")
        assistant._memory.add_assistant("Hi there!")
        assert len(assistant.memory.get_messages()) == 3

        # Clear via memory property
        assistant.memory.clear()

        # Should only have system message
        messages = assistant.memory.get_messages()
        assert len(messages) == 1
        assert messages[0]["role"] == "system"

    @patch('core.llm.providers.openai.OpenAI')
    def test_memory_add_messages(self, mock_openai_class, usage_tracker):
        """Test adding messages via memory property."""
        assistant = Assistant(
            system_prompt="Test",
            tracker=usage_tracker,
        )

        assistant.memory.add("user", "Custom message")

        messages = assistant.memory.get_messages()
        assert len(messages) == 2
        assert messages[1]["content"] == "Custom message"

    @patch('core.llm.providers.openai.OpenAI')
    def test_to_dict(self, mock_openai_class, usage_tracker):
        """Test serialization to dict."""
        assistant = Assistant(
            system_prompt="You are helpful.",
            call_type=CallType.COMMENTARY,
            game_id="game_123",
            tracker=usage_tracker,
        )

        data = assistant.to_dict()

        assert data["system_prompt"] == "You are helpful."
        assert data["model"] is not None
        assert "memory" in data
        assert "default_context" in data
        assert data["default_context"]["call_type"] == CallType.COMMENTARY
        assert data["default_context"]["game_id"] == "game_123"

    @patch('core.llm.providers.openai.OpenAI')
    def test_from_dict(self, mock_openai_class, usage_tracker):
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

        assistant = Assistant.from_dict(data, tracker=usage_tracker)

        assert assistant.system_message == "Restored prompt"
        assert assistant._default_context["game_id"] == "restored_game"
        assert assistant._default_context["player_name"] == "Joker"

        # Check memory was restored
        messages = assistant.memory.get_messages()
        assert len(messages) == 3  # system + 2 restored
        assert messages[1]["content"] == "Previous message"

    @patch('core.llm.providers.openai.OpenAI')
    def test_roundtrip_serialization(self, mock_openai_class, usage_tracker):
        """Test that to_dict/from_dict preserves state."""
        original = Assistant(
            system_prompt="Test prompt",
            call_type=CallType.PLAYER_DECISION,
            game_id="test_game",
            player_name="TestPlayer",
            tracker=usage_tracker,
        )

        # Add some memory
        original._memory.add_user("Message 1")
        original._memory.add_assistant("Response 1")

        # Serialize and deserialize
        data = original.to_dict()
        restored = Assistant.from_dict(data, tracker=usage_tracker)

        assert restored.system_message == "Test prompt"
        assert len(restored.memory.get_messages()) == len(original.memory.get_messages())

    @patch('core.llm.providers.openai.OpenAI')
    def test_json_format(self, mock_openai_class, usage_tracker):
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
            tracker=usage_tracker,
        )
        response = assistant.chat("What's your move?", json_format=True)

        # Verify JSON format was requested
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert response == '{"action": "raise", "amount": 100}'
