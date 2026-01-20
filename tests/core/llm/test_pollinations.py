"""Tests for Pollinations provider."""
import unittest
from unittest.mock import Mock, patch

from core.llm.providers.pollinations import PollinationsProvider, PollinationsImageResponse


class TestPollinationsProvider(unittest.TestCase):
    """Tests for PollinationsProvider class."""

    def setUp(self):
        """Set up test fixtures."""
        self.provider = PollinationsProvider(model="flux")

    def test_provider_name(self):
        """Test provider name."""
        self.assertEqual(self.provider.provider_name, "pollinations")

    def test_model(self):
        """Test model property."""
        self.assertEqual(self.provider.model, "flux")

    def test_image_model(self):
        """Test image_model property."""
        self.assertEqual(self.provider.image_model, "flux")

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

    @patch('core.llm.providers.pollinations.requests.Session')
    def test_generate_image_success(self, mock_session_class):
        """Test successful image generation."""
        # Set up mock with proper headers dict
        mock_session = Mock()
        mock_session.headers = {}  # Real dict for header assignment
        mock_session_class.return_value = mock_session

        # Create test image data (minimal PNG header)
        test_image_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100

        mock_response = Mock()
        mock_response.content = test_image_bytes
        mock_response.headers = {"Content-Type": "image/png"}
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        # Create provider with mocked session
        provider = PollinationsProvider(model="flux")
        provider._session = mock_session

        # Test
        result = provider.generate_image(
            prompt="A cartoon cat",
            size="512x512",
        )

        # Verify result
        self.assertIsInstance(result, PollinationsImageResponse)
        self.assertTrue(result.url.startswith("data:image/png;base64,"))
        self.assertEqual(result.model, "flux")
        self.assertEqual(result.size, "512x512")
        self.assertTrue(result.id.startswith("poll-"))

        # Verify API call
        mock_session.get.assert_called_once()
        call_args = mock_session.get.call_args
        self.assertIn("image.pollinations.ai", call_args[0][0])
        self.assertEqual(call_args[1]["params"]["model"], "flux")
        self.assertEqual(call_args[1]["params"]["width"], 512)
        self.assertEqual(call_args[1]["params"]["height"], 512)

    @patch('core.llm.providers.pollinations.requests.Session')
    def test_generate_image_with_default_size(self, mock_session_class):
        """Test image generation with default size."""
        mock_session = Mock()
        mock_session.headers = {}  # Real dict for header assignment
        mock_session_class.return_value = mock_session

        test_image_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        mock_response = Mock()
        mock_response.content = test_image_bytes
        mock_response.headers = {"Content-Type": "image/png"}
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        provider = PollinationsProvider(model="flux")
        provider._session = mock_session

        _result = provider.generate_image(prompt="A test image")

        # Verify default size
        call_args = mock_session.get.call_args
        self.assertEqual(call_args[1]["params"]["width"], 1024)
        self.assertEqual(call_args[1]["params"]["height"], 1024)

    @patch('core.llm.providers.pollinations.time.sleep')
    @patch('core.llm.providers.pollinations.requests.Session')
    def test_generate_image_timeout_with_retries(self, mock_session_class, mock_sleep):
        """Test timeout handling with retry logic."""
        import requests
        mock_session = Mock()
        mock_session.headers = {}  # Real dict for header assignment
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = requests.exceptions.Timeout()

        provider = PollinationsProvider(model="flux")
        provider._session = mock_session

        with self.assertRaises(Exception) as context:
            provider.generate_image(prompt="Test")

        # Should have retried MAX_RETRIES times
        self.assertIn("3 attempts", str(context.exception))
        # Should have called get 3 times (initial + 2 retries)
        self.assertEqual(mock_session.get.call_count, 3)
        # Should have slept between retries
        self.assertEqual(mock_sleep.call_count, 2)

    @patch('core.llm.providers.pollinations.time.sleep')
    @patch('core.llm.providers.pollinations.requests.Session')
    def test_generate_image_retry_succeeds_on_second_attempt(self, mock_session_class, mock_sleep):
        """Test that retry succeeds after initial timeout."""
        import requests
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session

        test_image_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        mock_response = Mock()
        mock_response.content = test_image_bytes
        mock_response.headers = {"Content-Type": "image/png"}
        mock_response.raise_for_status = Mock()

        # First call times out, second succeeds
        mock_session.get.side_effect = [
            requests.exceptions.Timeout(),
            mock_response
        ]

        provider = PollinationsProvider(model="flux")
        provider._session = mock_session

        result = provider.generate_image(prompt="Test")

        # Should succeed on second attempt
        self.assertIsInstance(result, PollinationsImageResponse)
        self.assertEqual(mock_session.get.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)

    @patch('core.llm.providers.pollinations.requests.Session')
    def test_generate_image_http_error_4xx_no_retry(self, mock_session_class):
        """Test that 4xx client errors are NOT retried."""
        import requests
        mock_session = Mock()
        mock_session.headers = {}  # Real dict for header assignment
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        http_error = requests.exceptions.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error
        mock_session.get.return_value = mock_response

        provider = PollinationsProvider(model="flux")
        provider._session = mock_session

        with self.assertRaises(Exception) as context:
            provider.generate_image(prompt="Test")

        self.assertIn("400", str(context.exception))
        # Should only call once - no retry for 4xx errors
        self.assertEqual(mock_session.get.call_count, 1)

    @patch('core.llm.providers.pollinations.time.sleep')
    @patch('core.llm.providers.pollinations.requests.Session')
    def test_generate_image_http_error_5xx_retries(self, mock_session_class, mock_sleep):
        """Test that 5xx server errors ARE retried."""
        import requests
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.status_code = 502
        mock_response.text = "Bad Gateway"
        http_error = requests.exceptions.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error
        mock_session.get.return_value = mock_response

        provider = PollinationsProvider(model="flux")
        provider._session = mock_session

        with self.assertRaises(Exception) as context:
            provider.generate_image(prompt="Test")

        self.assertIn("502", str(context.exception))
        # Should have retried (initial + 2 retries = 3 calls)
        self.assertEqual(mock_session.get.call_count, 3)

    @patch('core.llm.providers.pollinations.requests.Session')
    def test_generate_image_includes_random_seed(self, mock_session_class):
        """Test that generate_image includes a seed parameter for unique generations."""
        mock_session = Mock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session

        test_image_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        mock_response = Mock()
        mock_response.content = test_image_bytes
        mock_response.headers = {"Content-Type": "image/png"}
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        provider = PollinationsProvider(model="flux")
        provider._session = mock_session

        provider.generate_image(prompt="Test")

        # Verify seed parameter is included
        call_args = mock_session.get.call_args
        self.assertIn("seed", call_args[1]["params"])
        seed = call_args[1]["params"]["seed"]
        self.assertIsInstance(seed, int)
        self.assertGreaterEqual(seed, 1)
        self.assertLessEqual(seed, 999999999)

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
        """Test extract_image_url from PollinationsImageResponse."""
        response = PollinationsImageResponse(
            url="data:image/png;base64,abc123",
            id="poll-123",
            model="flux",
            size="512x512",
        )
        result = self.provider.extract_image_url(response)
        self.assertEqual(result, "data:image/png;base64,abc123")

    def test_extract_image_url_invalid_response(self):
        """Test extract_image_url with non-response object."""
        result = self.provider.extract_image_url(Mock())
        self.assertEqual(result, "")

    def test_extract_request_id(self):
        """Test extract_request_id from PollinationsImageResponse."""
        response = PollinationsImageResponse(
            url="data:image/png;base64,abc123",
            id="poll-abc123def456",
            model="flux",
            size="512x512",
        )
        result = self.provider.extract_request_id(response)
        self.assertEqual(result, "poll-abc123def456")

    def test_extract_request_id_invalid_response(self):
        """Test extract_request_id with non-response object."""
        result = self.provider.extract_request_id(Mock())
        self.assertEqual(result, "")


class TestPollinationsImageResponse(unittest.TestCase):
    """Tests for PollinationsImageResponse dataclass."""

    def test_create_response(self):
        """Test creating a response object."""
        response = PollinationsImageResponse(
            url="data:image/png;base64,test",
            id="poll-123",
            model="flux",
            size="1024x1024",
        )
        self.assertEqual(response.url, "data:image/png;base64,test")
        self.assertEqual(response.id, "poll-123")
        self.assertEqual(response.model, "flux")
        self.assertEqual(response.size, "1024x1024")


class TestPollinationsProviderWithApiKey(unittest.TestCase):
    """Tests for Pollinations provider with API key."""

    @patch.dict('os.environ', {'POLLINATIONS_API_KEY': 'test-key-123'})
    def test_api_key_from_env(self):
        """Test API key is loaded from environment."""
        provider = PollinationsProvider()
        self.assertEqual(provider._api_key, "test-key-123")

    def test_api_key_from_constructor(self):
        """Test API key can be passed in constructor."""
        provider = PollinationsProvider(api_key="explicit-key")
        self.assertEqual(provider._api_key, "explicit-key")


if __name__ == "__main__":
    unittest.main()
