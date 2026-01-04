"""Tests for LLMClient."""
import unittest
from unittest.mock import Mock, patch
import tempfile
import os

from core.llm import LLMClient, CallType, UsageTracker


class TestLLMClient(unittest.TestCase):
    """Tests for LLMClient class."""

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
    def test_complete_success(self, mock_openai_class):
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
        client = LLMClient(tracker=self.tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "Hi"}],
            call_type=CallType.PLAYER_DECISION,
        )

        self.assertEqual(response.content, "Hello!")
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.input_tokens, 10)
        self.assertEqual(response.output_tokens, 5)

    @patch('core.llm.providers.openai.OpenAI')
    def test_complete_with_json_format(self, mock_openai_class):
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

        client = LLMClient(tracker=self.tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "What's your move?"}],
            json_format=True,
        )

        # Verify JSON format was requested
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(response.content, '{"action": "fold"}')

    @patch('core.llm.providers.openai.OpenAI')
    def test_complete_error_handling(self, mock_openai_class):
        """Test error handling in completion."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API Error")

        client = LLMClient(tracker=self.tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "Hi"}],
        )

        self.assertEqual(response.content, "")
        self.assertEqual(response.status, "error")
        self.assertEqual(response.error_code, "Exception")

    @patch('core.llm.providers.openai.OpenAI')
    def test_tracking_context(self, mock_openai_class):
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

        client = LLMClient(tracker=self.tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "Hi"}],
            call_type=CallType.PLAYER_DECISION,
            game_id="game_123",
            owner_id="user_456",
            player_name="Batman",
        )

        # Verify tracking was recorded
        import sqlite3
        with sqlite3.connect(self.temp_db.name) as conn:
            cursor = conn.execute("SELECT call_type, game_id, owner_id, player_name FROM api_usage")
            row = cursor.fetchone()
            self.assertEqual(row[0], "player_decision")
            self.assertEqual(row[1], "game_123")
            self.assertEqual(row[2], "user_456")
            self.assertEqual(row[3], "Batman")


class TestCallType(unittest.TestCase):
    """Tests for CallType enum."""

    def test_call_type_values(self):
        """Test CallType enum has expected values."""
        self.assertEqual(CallType.PLAYER_DECISION.value, "player_decision")
        self.assertEqual(CallType.COMMENTARY.value, "commentary")
        self.assertEqual(CallType.IMAGE_GENERATION.value, "image_generation")

    def test_call_type_is_string(self):
        """Test CallType inherits from str."""
        self.assertIsInstance(CallType.PLAYER_DECISION, str)
        # The enum value is the string
        self.assertEqual(CallType.PLAYER_DECISION.value, "player_decision")
        # Can be used directly in string contexts
        self.assertIn("player_decision", f"{CallType.PLAYER_DECISION.value}")


if __name__ == "__main__":
    unittest.main()
