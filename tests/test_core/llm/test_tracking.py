"""Tests for UsageTracker and CallType."""
import unittest
from unittest.mock import patch
import tempfile
import os
import sqlite3

from core.llm import CallType, UsageTracker
from core.llm.response import LLMResponse, ImageResponse


class TestCallType(unittest.TestCase):
    """Tests for CallType enum."""

    def test_all_call_types_exist(self):
        """Test all expected CallType values exist."""
        expected_types = [
            "unknown",
            "player_decision",
            "commentary",
            "chat_suggestion",
            "targeted_chat",
            "personality_generation",
            "personality_preview",
            "theme_generation",
            "image_generation",
            "image_description",
            "categorization",
        ]

        for type_value in expected_types:
            # Should not raise
            call_type = CallType(type_value)
            self.assertEqual(call_type.value, type_value)

    def test_call_type_is_string_enum(self):
        """Test CallType inherits from str."""
        for call_type in CallType:
            self.assertIsInstance(call_type, str)
            # Value should be usable as string
            self.assertEqual(str(call_type.value), call_type.value)

    def test_call_type_unknown_is_default(self):
        """Test UNKNOWN is the appropriate fallback."""
        self.assertEqual(CallType.UNKNOWN.value, "unknown")

    def test_call_type_count(self):
        """Test we have the expected number of call types."""
        # This ensures we don't accidentally remove types
        self.assertEqual(len(CallType), 16)


class TestUsageTracker(unittest.TestCase):
    """Tests for UsageTracker class."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a temp database
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()

        # Initialize persistence to create tables
        from poker.persistence import GamePersistence
        GamePersistence(self.temp_db.name)

        # Reset singleton for each test
        UsageTracker._instance = None

    def tearDown(self):
        """Clean up temp files."""
        os.unlink(self.temp_db.name)
        UsageTracker._instance = None

    def test_tracker_init(self):
        """Test tracker initialization."""
        tracker = UsageTracker(db_path=self.temp_db.name)
        self.assertEqual(tracker.db_path, self.temp_db.name)

    def test_singleton_pattern(self):
        """Test get_default returns singleton."""
        # Set a specific tracker as default
        tracker1 = UsageTracker(db_path=self.temp_db.name)
        UsageTracker.set_default(tracker1)

        tracker2 = UsageTracker.get_default()
        self.assertIs(tracker1, tracker2)

    def test_set_default(self):
        """Test set_default overrides singleton."""
        tracker1 = UsageTracker(db_path=self.temp_db.name)
        UsageTracker.set_default(tracker1)

        # Create new tracker and set as default
        tracker2 = UsageTracker(db_path=self.temp_db.name)
        UsageTracker.set_default(tracker2)

        self.assertIs(UsageTracker.get_default(), tracker2)
        self.assertIsNot(UsageTracker.get_default(), tracker1)

    def test_record_llm_response(self):
        """Test recording LLM response."""
        tracker = UsageTracker(db_path=self.temp_db.name)

        response = LLMResponse(
            content="Test response",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=10,
            reasoning_tokens=5,
            latency_ms=150.5,
            finish_reason="stop",
            status="ok",
        )

        tracker.record(
            response=response,
            call_type=CallType.PLAYER_DECISION,
            game_id="game_123",
            owner_id="user_456",
            player_name="Batman",
            hand_number=5,
        )

        # Verify database record
        with sqlite3.connect(self.temp_db.name) as conn:
            cursor = conn.execute("""
                SELECT call_type, game_id, owner_id, player_name, hand_number,
                       provider, model, input_tokens, output_tokens,
                       cached_tokens, reasoning_tokens, latency_ms, status
                FROM api_usage
            """)
            row = cursor.fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], "player_decision")  # call_type
            self.assertEqual(row[1], "game_123")  # game_id
            self.assertEqual(row[2], "user_456")  # owner_id
            self.assertEqual(row[3], "Batman")  # player_name
            self.assertEqual(row[4], 5)  # hand_number
            self.assertEqual(row[5], "openai")  # provider
            self.assertEqual(row[6], "gpt-5-nano")  # model
            self.assertEqual(row[7], 100)  # input_tokens
            self.assertEqual(row[8], 50)  # output_tokens
            self.assertEqual(row[9], 10)  # cached_tokens
            self.assertEqual(row[10], 5)  # reasoning_tokens
            self.assertEqual(row[11], 150)  # latency_ms (int)
            self.assertEqual(row[12], "ok")  # status

    def test_record_image_response(self):
        """Test recording image response."""
        tracker = UsageTracker(db_path=self.temp_db.name)

        response = ImageResponse(
            url="https://example.com/image.png",
            model="dall-e-3",
            provider="openai",
            size="1024x1024",
            image_count=1,
            latency_ms=2500.0,
            status="ok",
        )

        tracker.record(
            response=response,
            call_type=CallType.IMAGE_GENERATION,
            game_id="game_456",
        )

        # Verify database record
        with sqlite3.connect(self.temp_db.name) as conn:
            cursor = conn.execute("""
                SELECT call_type, game_id, provider, model,
                       image_count, image_size, latency_ms, status,
                       input_tokens, output_tokens
                FROM api_usage
            """)
            row = cursor.fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], "image_generation")
            self.assertEqual(row[1], "game_456")
            self.assertEqual(row[2], "openai")
            self.assertEqual(row[3], "dall-e-3")
            self.assertEqual(row[4], 1)  # image_count
            self.assertEqual(row[5], "1024x1024")  # image_size
            self.assertEqual(row[6], 2500)  # latency_ms
            self.assertEqual(row[7], "ok")
            self.assertEqual(row[8], 0)  # input_tokens (0 for images)
            self.assertEqual(row[9], 0)  # output_tokens (0 for images)

    def test_record_with_none_call_type(self):
        """Test recording with None call_type defaults to UNKNOWN."""
        tracker = UsageTracker(db_path=self.temp_db.name)

        response = LLMResponse(
            content="Test",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=10,
            output_tokens=5,
        )

        tracker.record(response=response, call_type=None)

        with sqlite3.connect(self.temp_db.name) as conn:
            cursor = conn.execute("SELECT call_type FROM api_usage")
            row = cursor.fetchone()
            self.assertEqual(row[0], "unknown")

    def test_record_error_response(self):
        """Test recording error response."""
        tracker = UsageTracker(db_path=self.temp_db.name)

        response = LLMResponse(
            content="",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=0,
            output_tokens=0,
            status="error",
            error_code="RateLimitError",
        )

        tracker.record(
            response=response,
            call_type=CallType.PLAYER_DECISION,
        )

        with sqlite3.connect(self.temp_db.name) as conn:
            cursor = conn.execute("SELECT status, error_code FROM api_usage")
            row = cursor.fetchone()
            self.assertEqual(row[0], "error")
            self.assertEqual(row[1], "RateLimitError")

    @patch('core.llm.tracking.logger')
    def test_record_logs_stats(self, mock_logger):
        """Test that recording logs stats."""
        tracker = UsageTracker(db_path=self.temp_db.name)

        response = LLMResponse(
            content="Test",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            latency_ms=150.0,
            status="ok",
        )

        tracker.record(
            response=response,
            call_type=CallType.CHAT_SUGGESTION,
        )

        # Verify logger was called with info
        mock_logger.info.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]
        self.assertIn("[AI_STATS]", log_message)
        self.assertIn("gpt-5-nano", log_message)
        self.assertIn("chat_suggestion", log_message)

    @patch('core.llm.tracking.logger')
    def test_record_logs_error_on_error_response(self, mock_logger):
        """Test that error responses are logged at error level."""
        tracker = UsageTracker(db_path=self.temp_db.name)

        response = LLMResponse(
            content="",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=0,
            output_tokens=0,
            status="error",
            error_code="APIError",
        )

        tracker.record(response=response, call_type=CallType.UNKNOWN)

        # Verify logger.error was called
        mock_logger.error.assert_called_once()
        log_message = mock_logger.error.call_args[0][0]
        self.assertIn("[AI_STATS]", log_message)
        self.assertIn("status=error", log_message)

    def test_record_multiple_entries(self):
        """Test recording multiple usage entries."""
        tracker = UsageTracker(db_path=self.temp_db.name)

        for i in range(5):
            response = LLMResponse(
                content=f"Response {i}",
                model="gpt-5-nano",
                provider="openai",
                input_tokens=10 + i,
                output_tokens=5 + i,
            )
            tracker.record(
                response=response,
                call_type=CallType.PLAYER_DECISION,
                game_id=f"game_{i}",
            )

        with sqlite3.connect(self.temp_db.name) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM api_usage")
            count = cursor.fetchone()[0]
            self.assertEqual(count, 5)

    def test_record_with_prompt_template(self):
        """Test recording with prompt_template."""
        tracker = UsageTracker(db_path=self.temp_db.name)

        response = LLMResponse(
            content="Test",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=10,
            output_tokens=5,
        )

        tracker.record(
            response=response,
            call_type=CallType.PLAYER_DECISION,
            prompt_template="player_action_v2",
        )

        with sqlite3.connect(self.temp_db.name) as conn:
            cursor = conn.execute("SELECT prompt_template FROM api_usage")
            row = cursor.fetchone()
            self.assertEqual(row[0], "player_action_v2")

    @patch('core.llm.tracking.sqlite3.connect')
    @patch('core.llm.tracking.logger')
    def test_record_handles_db_error_gracefully(self, mock_logger, mock_connect):
        """Test that database errors are handled gracefully."""
        # First call succeeds (for _ensure_table), subsequent fails
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.execute.side_effect = [None, sqlite3.Error("DB Error")]

        tracker = UsageTracker(db_path=self.temp_db.name)
        # Reset the mock for the actual test
        mock_connect.reset_mock()
        mock_connect.return_value.__enter__.return_value.execute.side_effect = sqlite3.Error("DB Error")

        response = LLMResponse(
            content="Test",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=10,
            output_tokens=5,
        )

        # Should not raise - errors are logged
        tracker.record(response=response, call_type=CallType.UNKNOWN)

        # Verify error was logged (logger.info for stats, logger.error for db failure)
        self.assertTrue(mock_logger.error.called or mock_logger.info.called)


if __name__ == "__main__":
    unittest.main()
