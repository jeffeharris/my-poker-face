"""Guards for the FAST-tier latency fixes (LLM stall hardening, Phase 1).

Two root causes made FAST-tier flavor calls (narration, chat suggestions, beat
cleanup) stall for tens of seconds in prod:

1. On a toggleable model (xAI grok-4-fast) the LLMClient default reasoning_effort
   "low" silently selects the SLOW *reasoning* variant. Only "minimal" selects the
   fast non-reasoning variant. Narration clients didn't pass it.
2. The provider SDK clients defaulted to max_retries=2, which STACKED on the
   app-level retry loop — multiplying a per-attempt timeout into a multi-minute
   wall-clock stall. SDK retries are now disabled (the app loop owns retries).
"""

import pytest

from core.llm.providers.groq import GroqProvider
from core.llm.providers.openai import OpenAIProvider
from core.llm.providers.xai import XAIProvider


def test_grok4fast_minimal_selects_non_reasoning_variant():
    p = XAIProvider(model="grok-4-fast", reasoning_effort="minimal", api_key="t")
    assert p.model == "grok-4-fast-non-reasoning"


def test_grok4fast_low_selects_slow_reasoning_variant():
    # "low" is the LLMClient default — inheriting it is exactly the narration bug.
    p = XAIProvider(model="grok-4-fast", reasoning_effort="low", api_key="t")
    assert p.model == "grok-4-fast-reasoning"


def test_llmclient_default_effort_picks_reasoning_minimal_picks_fast(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "t")
    from core.llm import LLMClient

    # Default (no reasoning_effort) inherits LLMClient's "low" → slow variant.
    assert LLMClient(provider="xai", model="grok-4-fast")._provider.model == (
        "grok-4-fast-reasoning"
    )
    # Explicit "minimal" → fast variant (what the narration/chat/cleanup clients
    # now pass).
    assert (
        LLMClient(provider="xai", model="grok-4-fast", reasoning_effort="minimal")._provider.model
        == "grok-4-fast-non-reasoning"
    )


@pytest.mark.parametrize("provider_cls", [XAIProvider, OpenAIProvider, GroqProvider])
def test_sdk_retries_disabled(provider_cls):
    # The app-level loop owns retries; the SDK must not add its own (which would
    # multiply the per-call timeout into a multi-minute stall).
    p = provider_cls(api_key="t")
    assert p._client.max_retries == 0
