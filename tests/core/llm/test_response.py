"""Tests for LLMResponse and ImageResponse dataclasses."""
import unittest

from core.llm import LLMResponse, ImageResponse


class TestLLMResponse(unittest.TestCase):
    """Tests for LLMResponse dataclass."""

    def test_basic_response(self):
        """Test basic response creation."""
        response = LLMResponse(
            content="Hello, world!",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=10,
            output_tokens=5,
        )
        self.assertEqual(response.content, "Hello, world!")
        self.assertEqual(response.total_tokens, 15)
        self.assertFalse(response.is_error)
        self.assertFalse(response.was_truncated)

    def test_total_tokens(self):
        """Test total_tokens property."""
        response = LLMResponse(
            content="test",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
        )
        self.assertEqual(response.total_tokens, 150)

    def test_is_error_empty_content(self):
        """Test is_error with empty content."""
        response = LLMResponse(
            content="",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=10,
            output_tokens=0,
        )
        self.assertTrue(response.is_error)

    def test_is_error_status(self):
        """Test is_error with error status."""
        response = LLMResponse(
            content="error message",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=10,
            output_tokens=5,
            status="error",
        )
        self.assertTrue(response.is_error)

    def test_was_truncated(self):
        """Test was_truncated property."""
        response = LLMResponse(
            content="partial...",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=10,
            output_tokens=100,
            finish_reason="length",
        )
        self.assertTrue(response.was_truncated)

    def test_with_reasoning_tokens(self):
        """Test response with reasoning tokens."""
        response = LLMResponse(
            content="result",
            model="gpt-5-nano",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=30,
        )
        self.assertEqual(response.reasoning_tokens, 30)
        self.assertEqual(response.total_tokens, 150)


class TestImageResponse(unittest.TestCase):
    """Tests for ImageResponse dataclass."""

    def test_basic_response(self):
        """Test basic image response creation."""
        response = ImageResponse(
            url="https://example.com/image.png",
            model="dall-e-2",
            provider="openai",
            size="1024x1024",
        )
        self.assertEqual(response.url, "https://example.com/image.png")
        self.assertEqual(response.size, "1024x1024")
        self.assertFalse(response.is_error)

    def test_is_error_empty_url(self):
        """Test is_error with empty URL."""
        response = ImageResponse(
            url="",
            model="dall-e-2",
            provider="openai",
            size="1024x1024",
        )
        self.assertTrue(response.is_error)

    def test_is_error_status(self):
        """Test is_error with error status."""
        response = ImageResponse(
            url="https://example.com/image.png",
            model="dall-e-2",
            provider="openai",
            size="1024x1024",
            status="error",
        )
        self.assertTrue(response.is_error)


if __name__ == "__main__":
    unittest.main()
