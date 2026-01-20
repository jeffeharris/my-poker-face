"""Tests for Runware provider."""
import unittest
from unittest.mock import Mock, patch

from core.llm.providers.runware import RunwareProvider, RunwareImageResponse, round_to_multiple_of_64


class TestRoundToMultipleOf64(unittest.TestCase):
    """Tests for the round_to_multiple_of_64 helper function."""

    def test_round_exact_multiple(self):
        """Test rounding exact multiples of 64."""
        self.assertEqual(round_to_multiple_of_64(512), 512)
        self.assertEqual(round_to_multiple_of_64(1024), 1024)
        self.assertEqual(round_to_multiple_of_64(128), 128)

    def test_round_up(self):
        """Test rounding up."""
        self.assertEqual(round_to_multiple_of_64(550), 576)  # 576 = 9 * 64
        self.assertEqual(round_to_multiple_of_64(1000), 1024)

    def test_round_down(self):
        """Test rounding down."""
        self.assertEqual(round_to_multiple_of_64(500), 512)  # 512 = 8 * 64
        self.assertEqual(round_to_multiple_of_64(520), 512)


class TestRunwareProvider(unittest.TestCase):
    """Tests for RunwareProvider class."""

    def setUp(self):
        """Set up test fixtures."""
        self.provider = RunwareProvider(model="runware:101@1", api_key="test-key")

    def test_provider_name(self):
        """Test provider name."""
        self.assertEqual(self.provider.provider_name, "runware")

    def test_model(self):
        """Test model property."""
        self.assertEqual(self.provider.model, "runware:101@1")

    def test_image_model(self):
        """Test image_model property."""
        self.assertEqual(self.provider.image_model, "runware:101@1")

    def test_reasoning_effort_is_none(self):
        """Test reasoning_effort is None for image-only provider."""
        self.assertIsNone(self.provider.reasoning_effort)

    def test_complete_raises_not_implemented(self):
        """Test that complete() raises NotImplementedError."""
        with self.assertRaises(NotImplementedError) as context:
            self.provider.complete(
                messages=[{"role": "user", "content": "Hello"}]
            )
        self.assertIn("image-only provider", str(context.exception))

    @patch('core.llm.providers.runware.requests.Session')
    def test_generate_image_success(self, mock_session_class):
        """Test successful image generation."""
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [{
                "taskType": "imageInference",
                "taskUUID": "test-uuid-123",
                "imageUUID": "img-uuid-456",
                "imageURL": "https://im.runware.ai/image/test.png"
            }]
        }
        mock_response.raise_for_status = Mock()
        mock_session.post.return_value = mock_response

        provider = RunwareProvider(model="runware:101@1", api_key="test-key")
        provider._session = mock_session

        result = provider.generate_image(
            prompt="A cartoon cat",
            size="512x512",
        )

        self.assertIsInstance(result, RunwareImageResponse)
        self.assertEqual(result.url, "https://im.runware.ai/image/test.png")
        self.assertEqual(result.model, "runware:101@1")
        self.assertEqual(result.size, "512x512")

        # Verify API call
        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        self.assertEqual(call_args[0][0], "https://api.runware.ai/v1")

        # Verify request payload
        payload = call_args[1]["json"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["taskType"], "imageInference")
        self.assertEqual(payload[0]["positivePrompt"], "A cartoon cat")
        self.assertEqual(payload[0]["width"], 512)
        self.assertEqual(payload[0]["height"], 512)
        self.assertEqual(payload[0]["model"], "runware:101@1")

    @patch('core.llm.providers.runware.requests.Session')
    def test_generate_image_with_default_size(self, mock_session_class):
        """Test image generation with default size."""
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [{
                "imageURL": "https://im.runware.ai/image/test.png"
            }]
        }
        mock_response.raise_for_status = Mock()
        mock_session.post.return_value = mock_response

        provider = RunwareProvider(model="runware:101@1", api_key="test-key")
        provider._session = mock_session

        _result = provider.generate_image(prompt="A test image")

        # Verify default size
        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        self.assertEqual(payload[0]["width"], 512)
        self.assertEqual(payload[0]["height"], 512)

    @patch('core.llm.providers.runware.requests.Session')
    def test_generate_image_size_rounding(self, mock_session_class):
        """Test that non-64-multiple sizes are rounded."""
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [{
                "imageURL": "https://im.runware.ai/image/test.png"
            }]
        }
        mock_response.raise_for_status = Mock()
        mock_session.post.return_value = mock_response

        provider = RunwareProvider(model="runware:101@1", api_key="test-key")
        provider._session = mock_session

        # 1024x1024 should round correctly
        _result = provider.generate_image(prompt="Test", size="1024x1024")

        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        self.assertEqual(payload[0]["width"], 1024)
        self.assertEqual(payload[0]["height"], 1024)

    @patch('core.llm.providers.runware.requests.Session')
    def test_generate_image_size_bounds(self, mock_session_class):
        """Test that sizes are clamped to 128-2048 range."""
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [{
                "imageURL": "https://im.runware.ai/image/test.png"
            }]
        }
        mock_response.raise_for_status = Mock()
        mock_session.post.return_value = mock_response

        provider = RunwareProvider(model="runware:101@1", api_key="test-key")
        provider._session = mock_session

        # Test size below minimum
        provider.generate_image(prompt="Test", size="50x50")
        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        self.assertEqual(payload[0]["width"], 128)
        self.assertEqual(payload[0]["height"], 128)

    @patch('core.llm.providers.runware.time.sleep')
    @patch('core.llm.providers.runware.requests.Session')
    def test_generate_image_timeout_with_retries(self, mock_session_class, mock_sleep):
        """Test timeout handling with retry logic."""
        import requests
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session
        mock_session.post.side_effect = requests.exceptions.Timeout()

        provider = RunwareProvider(model="runware:101@1", api_key="test-key")
        provider._session = mock_session

        with self.assertRaises(Exception) as context:
            provider.generate_image(prompt="Test")

        self.assertIn("3 attempts", str(context.exception))
        self.assertEqual(mock_session.post.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch('core.llm.providers.runware.requests.Session')
    def test_generate_image_api_error_in_response(self, mock_session_class):
        """Test handling of errors in API response body."""
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.json.return_value = {
            "errors": [{
                "message": "Invalid API key"
            }]
        }
        mock_response.raise_for_status = Mock()
        mock_session.post.return_value = mock_response

        provider = RunwareProvider(model="runware:101@1", api_key="bad-key")
        provider._session = mock_session

        with self.assertRaises(Exception) as context:
            provider.generate_image(prompt="Test")

        self.assertIn("Invalid API key", str(context.exception))

    @patch('core.llm.providers.runware.requests.Session')
    def test_generate_image_empty_data_response(self, mock_session_class):
        """Test handling of empty data in response."""
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status = Mock()
        mock_session.post.return_value = mock_response

        provider = RunwareProvider(model="runware:101@1", api_key="test-key")
        provider._session = mock_session

        with self.assertRaises(Exception) as context:
            provider.generate_image(prompt="Test")

        self.assertIn("empty data", str(context.exception))

    @patch('core.llm.providers.runware.requests.Session')
    def test_generate_image_http_error_4xx_no_retry(self, mock_session_class):
        """Test that 4xx client errors are NOT retried."""
        import requests
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        http_error = requests.exceptions.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error
        mock_session.post.return_value = mock_response

        provider = RunwareProvider(model="runware:101@1", api_key="test-key")
        provider._session = mock_session

        with self.assertRaises(Exception) as context:
            provider.generate_image(prompt="Test")

        self.assertIn("401", str(context.exception))
        self.assertEqual(mock_session.post.call_count, 1)

    @patch('core.llm.providers.runware.time.sleep')
    @patch('core.llm.providers.runware.requests.Session')
    def test_generate_image_http_error_5xx_retries(self, mock_session_class, mock_sleep):
        """Test that 5xx server errors ARE retried."""
        import requests
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        http_error = requests.exceptions.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error
        mock_session.post.return_value = mock_response

        provider = RunwareProvider(model="runware:101@1", api_key="test-key")
        provider._session = mock_session

        with self.assertRaises(Exception) as context:
            provider.generate_image(prompt="Test")

        self.assertIn("503", str(context.exception))
        self.assertEqual(mock_session.post.call_count, 3)

    def test_extract_usage_returns_zeros(self):
        """Test extract_usage returns zeros (no tokens for images)."""
        result = self.provider.extract_usage(Mock())
        self.assertEqual(result["input_tokens"], 0)
        self.assertEqual(result["output_tokens"], 0)
        self.assertEqual(result["cached_tokens"], 0)
        self.assertEqual(result["reasoning_tokens"], 0)

    def test_extract_content_returns_empty(self):
        """Test extract_content returns empty string."""
        result = self.provider.extract_content(Mock())
        self.assertEqual(result, "")

    def test_extract_finish_reason(self):
        """Test extract_finish_reason returns 'complete'."""
        result = self.provider.extract_finish_reason(Mock())
        self.assertEqual(result, "complete")

    def test_extract_image_url(self):
        """Test extract_image_url from RunwareImageResponse."""
        response = RunwareImageResponse(
            url="https://im.runware.ai/image/test.png",
            id="task-123",
            model="runware:101@1",
            size="512x512",
        )
        result = self.provider.extract_image_url(response)
        self.assertEqual(result, "https://im.runware.ai/image/test.png")

    def test_extract_image_url_invalid_response(self):
        """Test extract_image_url with non-response object."""
        result = self.provider.extract_image_url(Mock())
        self.assertEqual(result, "")

    def test_extract_request_id(self):
        """Test extract_request_id from RunwareImageResponse."""
        response = RunwareImageResponse(
            url="https://im.runware.ai/image/test.png",
            id="task-uuid-abc123",
            model="runware:101@1",
            size="512x512",
        )
        result = self.provider.extract_request_id(response)
        self.assertEqual(result, "task-uuid-abc123")

    def test_extract_request_id_invalid_response(self):
        """Test extract_request_id with non-response object."""
        result = self.provider.extract_request_id(Mock())
        self.assertEqual(result, "")


class TestRunwareImageResponse(unittest.TestCase):
    """Tests for RunwareImageResponse dataclass."""

    def test_create_response(self):
        """Test creating a response object."""
        response = RunwareImageResponse(
            url="https://im.runware.ai/image/test.png",
            id="task-123",
            model="runware:100@1",
            size="1024x1024",
        )
        self.assertEqual(response.url, "https://im.runware.ai/image/test.png")
        self.assertEqual(response.id, "task-123")
        self.assertEqual(response.model, "runware:100@1")
        self.assertEqual(response.size, "1024x1024")


class TestRunwareProviderWithApiKey(unittest.TestCase):
    """Tests for Runware provider with API key."""

    @patch.dict('os.environ', {'RUNWARE_API_KEY': 'env-test-key-123'})
    def test_api_key_from_env(self):
        """Test API key is loaded from environment."""
        provider = RunwareProvider()
        self.assertEqual(provider._api_key, "env-test-key-123")

    def test_api_key_from_constructor(self):
        """Test API key can be passed in constructor."""
        provider = RunwareProvider(api_key="explicit-key")
        self.assertEqual(provider._api_key, "explicit-key")

    @patch.dict('os.environ', {}, clear=True)
    def test_missing_api_key_logs_warning(self):
        """Test that missing API key logs a warning."""
        with patch('core.llm.providers.runware.logger') as mock_logger:
            _provider = RunwareProvider()
            mock_logger.warning.assert_called_once()
            self.assertIn("RUNWARE_API_KEY", mock_logger.warning.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
