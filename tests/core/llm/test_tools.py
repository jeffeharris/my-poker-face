"""Tests for tool calling support in LLMClient."""
import json
import unittest
from unittest.mock import Mock, patch
import tempfile
import os

from core.llm import LLMClient, UsageTracker


class TestToolCalling(unittest.TestCase):
    """Tests for tool calling functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()

        from poker.persistence import GamePersistence
        GamePersistence(self.temp_db.name)

        self.tracker = UsageTracker(db_path=self.temp_db.name)

    def tearDown(self):
        """Clean up temp files."""
        os.unlink(self.temp_db.name)

    @patch('core.llm.providers.openai.OpenAI')
    def test_complete_without_tools(self, mock_openai_class):
        """Test completion works normally without tools."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.choices[0].message.tool_calls = None
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.completion_tokens_details = None
        mock_response.usage.prompt_tokens_details = None
        mock_response.id = "test-123"

        mock_client.chat.completions.create.return_value = mock_response

        client = LLMClient(tracker=self.tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "Hi"}],
        )

        self.assertEqual(response.content, "Hello!")
        self.assertEqual(response.status, "ok")
        self.assertIsNone(response.tool_calls)

    @patch('core.llm.providers.openai.OpenAI')
    def test_complete_with_tool_call(self, mock_openai_class):
        """Test completion with tool calls that get executed."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        # First response: model requests a tool call
        mock_tool_call = Mock()
        mock_tool_call.id = "call_123"
        mock_tool_call.type = "function"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "get_weather"
        mock_tool_call.function.arguments = '{"location": "NYC"}'

        mock_response_1 = Mock()
        mock_response_1.choices = [Mock()]
        mock_response_1.choices[0].message.content = ""
        mock_response_1.choices[0].message.tool_calls = [mock_tool_call]
        mock_response_1.choices[0].finish_reason = "tool_calls"
        mock_response_1.usage = Mock()
        mock_response_1.usage.prompt_tokens = 10
        mock_response_1.usage.completion_tokens = 5
        mock_response_1.usage.completion_tokens_details = None
        mock_response_1.usage.prompt_tokens_details = None
        mock_response_1.id = "test-123"

        # Second response: model gives final answer after tool result
        mock_response_2 = Mock()
        mock_response_2.choices = [Mock()]
        mock_response_2.choices[0].message.content = "The weather in NYC is sunny."
        mock_response_2.choices[0].message.tool_calls = None
        mock_response_2.choices[0].finish_reason = "stop"
        mock_response_2.usage = Mock()
        mock_response_2.usage.prompt_tokens = 20
        mock_response_2.usage.completion_tokens = 10
        mock_response_2.usage.completion_tokens_details = None
        mock_response_2.usage.prompt_tokens_details = None
        mock_response_2.id = "test-456"

        mock_client.chat.completions.create.side_effect = [mock_response_1, mock_response_2]

        # Tool executor
        def tool_executor(name, args):
            if name == "get_weather":
                return json.dumps({"weather": "sunny", "temp": 72})
            return json.dumps({"error": "unknown tool"})

        # Define a tool
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string"}
                    },
                    "required": ["location"]
                }
            }
        }]

        client = LLMClient(tracker=self.tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "What's the weather in NYC?"}],
            tools=tools,
            tool_executor=tool_executor,
        )

        self.assertEqual(response.content, "The weather in NYC is sunny.")
        self.assertEqual(response.status, "ok")
        # Token usage should be aggregated
        self.assertEqual(response.input_tokens, 30)  # 10 + 20
        self.assertEqual(response.output_tokens, 15)  # 5 + 10

    @patch('core.llm.providers.openai.OpenAI')
    def test_complete_with_tool_no_executor(self, mock_openai_class):
        """Test that tool calls without executor returns tool_calls in response."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        mock_tool_call = Mock()
        mock_tool_call.id = "call_123"
        mock_tool_call.type = "function"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "get_weather"
        mock_tool_call.function.arguments = '{"location": "NYC"}'

        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = ""
        mock_response.choices[0].message.tool_calls = [mock_tool_call]
        mock_response.choices[0].finish_reason = "tool_calls"
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.completion_tokens_details = None
        mock_response.usage.prompt_tokens_details = None
        mock_response.id = "test-123"

        mock_client.chat.completions.create.return_value = mock_response

        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {}}
            }
        }]

        client = LLMClient(tracker=self.tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "What's the weather?"}],
            tools=tools,
            # No tool_executor provided
        )

        # Should return the tool calls without executing them
        self.assertIsNotNone(response.tool_calls)
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0]["function"]["name"], "get_weather")

    @patch('core.llm.providers.openai.OpenAI')
    def test_tool_execution_error_handling(self, mock_openai_class):
        """Test that tool execution errors are handled gracefully."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        mock_tool_call = Mock()
        mock_tool_call.id = "call_123"
        mock_tool_call.type = "function"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "failing_tool"
        mock_tool_call.function.arguments = '{}'

        mock_response_1 = Mock()
        mock_response_1.choices = [Mock()]
        mock_response_1.choices[0].message.content = ""
        mock_response_1.choices[0].message.tool_calls = [mock_tool_call]
        mock_response_1.choices[0].finish_reason = "tool_calls"
        mock_response_1.usage = Mock()
        mock_response_1.usage.prompt_tokens = 10
        mock_response_1.usage.completion_tokens = 5
        mock_response_1.usage.completion_tokens_details = None
        mock_response_1.usage.prompt_tokens_details = None
        mock_response_1.id = "test-123"

        mock_response_2 = Mock()
        mock_response_2.choices = [Mock()]
        mock_response_2.choices[0].message.content = "Sorry, there was an error."
        mock_response_2.choices[0].message.tool_calls = None
        mock_response_2.choices[0].finish_reason = "stop"
        mock_response_2.usage = Mock()
        mock_response_2.usage.prompt_tokens = 15
        mock_response_2.usage.completion_tokens = 8
        mock_response_2.usage.completion_tokens_details = None
        mock_response_2.usage.prompt_tokens_details = None
        mock_response_2.id = "test-456"

        mock_client.chat.completions.create.side_effect = [mock_response_1, mock_response_2]

        def failing_executor(name, args):
            raise ValueError("Tool execution failed!")

        tools = [{
            "type": "function",
            "function": {
                "name": "failing_tool",
                "description": "A tool that fails",
                "parameters": {"type": "object", "properties": {}}
            }
        }]

        client = LLMClient(tracker=self.tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "Run the failing tool"}],
            tools=tools,
            tool_executor=failing_executor,
        )

        # Should still get a response (model sees the error and responds)
        self.assertEqual(response.content, "Sorry, there was an error.")
        self.assertEqual(response.status, "ok")


    @patch('core.llm.providers.deepseek.OpenAI')
    def test_reasoning_content_preserved_in_tool_loop(self, mock_openai_class):
        """Test that reasoning_content is preserved in assistant messages during tool loop.

        This is critical for DeepSeek thinking mode - the API requires reasoning_content
        to be present in all assistant messages when thinking mode is enabled.
        """
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        # First response: model requests a tool call WITH reasoning_content
        mock_tool_call = Mock()
        mock_tool_call.id = "call_123"
        mock_tool_call.type = "function"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "get_weather"
        mock_tool_call.function.arguments = '{"location": "NYC"}'

        mock_response_1 = Mock()
        mock_response_1.choices = [Mock()]
        mock_response_1.choices[0].message.content = ""
        mock_response_1.choices[0].message.tool_calls = [mock_tool_call]
        mock_response_1.choices[0].message.reasoning_content = "Let me think about this..."
        mock_response_1.choices[0].finish_reason = "tool_calls"
        mock_response_1.usage = Mock()
        mock_response_1.usage.prompt_tokens = 10
        mock_response_1.usage.completion_tokens = 5
        mock_response_1.usage.completion_tokens_details = None
        mock_response_1.usage.prompt_cache_hit_tokens = 0
        mock_response_1.id = "test-123"

        # Second response: final answer with reasoning
        mock_response_2 = Mock()
        mock_response_2.choices = [Mock()]
        mock_response_2.choices[0].message.content = "It's sunny in NYC."
        mock_response_2.choices[0].message.tool_calls = None
        mock_response_2.choices[0].message.reasoning_content = "Based on the weather data..."
        mock_response_2.choices[0].finish_reason = "stop"
        mock_response_2.usage = Mock()
        mock_response_2.usage.prompt_tokens = 20
        mock_response_2.usage.completion_tokens = 10
        mock_response_2.usage.completion_tokens_details = None
        mock_response_2.usage.prompt_cache_hit_tokens = 0
        mock_response_2.id = "test-456"

        mock_client.chat.completions.create.side_effect = [mock_response_1, mock_response_2]

        def tool_executor(name, args):
            return json.dumps({"weather": "sunny"})

        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {"location": {"type": "string"}}}
            }
        }]

        # Use DeepSeek provider which implements extract_reasoning_content
        client = LLMClient(provider="deepseek", model="deepseek-chat", tracker=self.tracker)
        response = client.complete(
            messages=[{"role": "user", "content": "Weather in NYC?"}],
            tools=tools,
            tool_executor=tool_executor,
        )

        # Verify response has reasoning_content from the final response
        self.assertEqual(response.content, "It's sunny in NYC.")
        self.assertEqual(response.reasoning_content, "Based on the weather data...")

        # Verify the second API call includes reasoning_content in the assistant message
        calls = mock_client.chat.completions.create.call_args_list
        self.assertEqual(len(calls), 2)

        # Check the messages sent in the second call
        second_call_messages = calls[1][1]["messages"]
        # Find the assistant message (should be before the tool result)
        assistant_msgs = [m for m in second_call_messages if m.get("role") == "assistant"]
        self.assertEqual(len(assistant_msgs), 1)
        self.assertEqual(assistant_msgs[0].get("reasoning_content"), "Let me think about this...")


if __name__ == "__main__":
    unittest.main()
