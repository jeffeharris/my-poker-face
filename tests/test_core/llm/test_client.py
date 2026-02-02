"""Tests for LLMClient."""
import sqlite3

from unittest.mock import Mock, patch

from core.llm import LLMClient, CallType, UsageTracker


class TestLLMClient:
    """Tests for LLMClient class."""

    @patch('core.llm.providers.openai.OpenAI')
    def test_complete_success(self, mock_openai_class, usage_tracker, db_path):
        """Test successful completion."""
        # Set up mock
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

        # Test
        client = LLMClient(tracker=usage_tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "Hi"}],
            call_type=CallType.PLAYER_DECISION,
        )

        assert response.content == "Hello!"
        assert response.status == "ok"
        assert response.input_tokens == 10
        assert response.output_tokens == 5

    @patch('core.llm.providers.openai.OpenAI')
    def test_complete_with_json_format(self, mock_openai_class, usage_tracker):
        """Test completion with JSON format."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = '{"action": "fold"}'
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.completion_tokens_details = None
        mock_response.usage.prompt_tokens_details = None

        mock_client.chat.completions.create.return_value = mock_response

        client = LLMClient(tracker=usage_tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "What's your move?"}],
            json_format=True,
        )

        # Verify JSON format was requested
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert response.content == '{"action": "fold"}'

    @patch('core.llm.providers.openai.OpenAI')
    def test_complete_error_handling(self, mock_openai_class, usage_tracker):
        """Test error handling in completion."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API Error")

        client = LLMClient(tracker=usage_tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert response.content == ""
        assert response.status == "error"
        assert response.error_code == "Exception"

    @patch('core.llm.providers.openai.OpenAI')
    def test_tracking_context(self, mock_openai_class, usage_tracker, db_path):
        """Test that tracking context is passed correctly."""
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

        client = LLMClient(tracker=usage_tracker)
        client.complete(
            messages=[{"role": "user", "content": "Hi"}],
            call_type=CallType.PLAYER_DECISION,
            game_id="game_123",
            owner_id="user_456",
            player_name="Batman",
        )

        # Verify tracking was recorded
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("SELECT call_type, game_id, owner_id, player_name FROM api_usage")
            row = cursor.fetchone()
            assert row[0] == "player_decision"
            assert row[1] == "game_123"
            assert row[2] == "user_456"
            assert row[3] == "Batman"


class TestCallType:
    """Tests for CallType enum."""

    def test_call_type_values(self):
        """Test CallType enum has expected values."""
        assert CallType.PLAYER_DECISION.value == "player_decision"
        assert CallType.COMMENTARY.value == "commentary"
        assert CallType.IMAGE_GENERATION.value == "image_generation"

    def test_call_type_is_string(self):
        """Test CallType inherits from str."""
        assert isinstance(CallType.PLAYER_DECISION, str)
        assert CallType.PLAYER_DECISION.value == "player_decision"
        assert "player_decision" in f"{CallType.PLAYER_DECISION.value}"
