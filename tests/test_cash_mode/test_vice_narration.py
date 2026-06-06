"""Tests for the vice narration LLM call.

Covers the JSON parse, the fallback path on LLM/network failure, and
the bucket-in-prompt wiring. Narration is flavor-only now — the
duration bucket is chosen system-side and passed *into* the narrator;
`narrate_vice` returns just the line. Uses a mocked LLMClient so no
real API calls fire.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.vice_narration import narrate_vice


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


def _patch_client(content: str):
    """Patch LLMClient + settings so narrate_vice uses our fake content."""
    fake_client = MagicMock()
    fake_client.complete.return_value = _FakeResponse(content)
    # Patch both the class and the settings module attributes that
    # narrate_inner reaches into.
    return patch(
        "core.llm.LLMClient",
        return_value=fake_client,
    )


def _patch_client_raises(exc: Exception):
    fake_client = MagicMock()
    fake_client.complete.side_effect = exc
    return patch(
        "core.llm.LLMClient",
        return_value=fake_client,
    )


def test_happy_path_returns_narration():
    content = json.dumps({"narration": "Napoleon commissioned an oversized bronze bust"})
    with _patch_client(content):
        narration = narrate_vice(
            "napoleon",
            2500,
            {"confidence": 0.7, "composure": 0.7, "energy": 0.6},
            "long",
        )
    assert narration == "Napoleon commissioned an oversized bronze bust"


def test_extra_duration_field_is_ignored():
    # The model may still echo a duration; flavor-only parse ignores it.
    content = json.dumps({"narration": "Hemingway hit the bar", "duration": "forever"})
    with _patch_client(content):
        narration = narrate_vice("hemingway", 500, None, "short")
    assert narration == "Hemingway hit the bar"


def test_empty_narration_falls_back_to_template():
    content = json.dumps({"narration": "  "})
    with _patch_client(content):
        narration = narrate_vice("buddha", 200, None, "long")
    # Templated fallback contains the personality id and amount
    assert "buddha" in narration
    assert "$200" in narration


def test_non_json_response_falls_back_to_template():
    with _patch_client("not valid json at all"):
        narration = narrate_vice("zoidberg", 1000, None, "medium")
    assert "zoidberg" in narration
    assert "$1,000" in narration


def test_llm_failure_falls_back_to_template():
    with _patch_client_raises(RuntimeError("network down")):
        narration = narrate_vice("offline_ai", 750, None, "short")
    assert "offline_ai" in narration
    assert "$750" in narration


def test_strips_stray_quotes_from_narration():
    content = json.dumps({"narration": "\"Napoleon did a thing\""})
    with _patch_client(content):
        narration = narrate_vice("napoleon", 500, None, "short")
    assert not narration.startswith('"')
    assert not narration.endswith('"')


def test_duration_bucket_is_passed_into_prompt():
    """The chosen duration bucket should appear in the user prompt so
    the flavor line is consistent with it."""
    content = json.dumps({"narration": "x"})
    with patch("core.llm.LLMClient") as ClientCls:
        ClientCls.return_value.complete.return_value = _FakeResponse(content)
        narrate_vice("napoleon", 2500, None, "long")
        call = ClientCls.return_value.complete.call_args
        messages = call.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "long" in user_msg["content"].lower()


def test_passes_personality_config_to_prompt():
    """The user prompt should include personality fields when the repo
    is provided. Verifies the LLM client is called with a message
    containing the personality's style / anchors."""
    content = json.dumps({"narration": "x"})
    personality_repo = MagicMock()
    personality_repo.load_personality_by_id.return_value = {
        "name": "Napoleon",
        "play_style": "aggressive opportunist",
        "default_attitude": "imperious",
        "anchors": {"ego": 0.9, "baseline_aggression": 0.8},
        "verbal_tics": ["mais bien sur"],
    }
    with patch("core.llm.LLMClient") as ClientCls:
        ClientCls.return_value.complete.return_value = _FakeResponse(content)
        narrate_vice(
            "napoleon",
            2500,
            None,
            "medium",
            personality_repo=personality_repo,
        )
        # Inspect the user message that was sent
        call = ClientCls.return_value.complete.call_args
        messages = call.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Napoleon" in user_msg["content"]
        assert "aggressive opportunist" in user_msg["content"]
        assert "ego" in user_msg["content"]
        assert "mais bien sur" in user_msg["content"]
