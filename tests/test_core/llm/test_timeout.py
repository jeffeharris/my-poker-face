"""PRH-18: per-call timeout threads LLMClient -> provider -> SDK create().

A short in-game/ticker timeout must reach the underlying SDK call so a stalled
provider fails fast; the default (no timeout) must preserve prior behavior (no
timeout kwarg, so the shared client's long read timeout still applies to
batch/experiment work).
"""

from unittest.mock import Mock, patch

from core.llm import CallType, LLMClient


def _mock_openai(mock_openai_class):
    mock_client = Mock()
    mock_openai_class.return_value = mock_client
    resp = Mock()
    resp.choices = [Mock()]
    resp.choices[0].message.content = "ok"
    resp.choices[0].finish_reason = "stop"
    resp.usage = Mock()
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    resp.usage.completion_tokens_details = None
    resp.usage.prompt_tokens_details = None
    mock_client.chat.completions.create.return_value = resp
    return mock_client


@patch('core.llm.providers.openai.OpenAI')
def test_default_timeout_threaded_to_sdk(mock_openai_class, usage_tracker):
    mock_client = _mock_openai(mock_openai_class)
    client = LLMClient(tracker=usage_tracker, default_timeout=12.5)
    client.complete(
        messages=[{"role": "user", "content": "hi"}], call_type=CallType.PLAYER_DECISION
    )
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs.get("timeout") == 12.5


@patch('core.llm.providers.openai.OpenAI')
def test_per_call_timeout_overrides_default(mock_openai_class, usage_tracker):
    mock_client = _mock_openai(mock_openai_class)
    client = LLMClient(tracker=usage_tracker, default_timeout=12.5)
    client.complete(
        messages=[{"role": "user", "content": "hi"}],
        call_type=CallType.PLAYER_DECISION,
        timeout=3.0,
    )
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs.get("timeout") == 3.0


@patch('core.llm.providers.openai.OpenAI')
def test_no_timeout_is_not_passed_to_sdk(mock_openai_class, usage_tracker):
    """Default path is byte-for-byte the old behavior: no timeout kwarg."""
    mock_client = _mock_openai(mock_openai_class)
    client = LLMClient(tracker=usage_tracker)
    client.complete(
        messages=[{"role": "user", "content": "hi"}], call_type=CallType.PLAYER_DECISION
    )
    _, kwargs = mock_client.chat.completions.create.call_args
    assert "timeout" not in kwargs


def test_ticker_timeout_is_tighter_than_ingame():
    """PRH-21: the world-ticker narration (shared greenlet, all-users blast
    radius) must be bounded tighter than a single in-game decision (PRH-18)."""
    from core.llm.config import INGAME_LLM_TIMEOUT_SECONDS, TICKER_LLM_TIMEOUT_SECONDS

    assert 0 < TICKER_LLM_TIMEOUT_SECONDS < INGAME_LLM_TIMEOUT_SECONDS
