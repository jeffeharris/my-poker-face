"""Tests for the vice narration LLM call.

Covers the JSON parse, the fallback path on LLM/network failure, and
the duration-bucket normalization. Uses a mocked LLMClient so no
real API calls fire.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

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


def test_happy_path_returns_narration_and_duration():
    content = json.dumps({
        "narration": "Napoleon commissioned an oversized bronze bust",
        "duration": "long",
    })
    with _patch_client(content):
        narration, bucket = narrate_vice(
            "napoleon", 2500, {"confidence": 0.7, "composure": 0.7, "energy": 0.6},
        )
    assert narration == "Napoleon commissioned an oversized bronze bust"
    assert bucket == "long"


def test_unknown_duration_falls_back_to_medium():
    content = json.dumps({
        "narration": "Hemingway did something",
        "duration": "forever",  # not a recognized bucket
    })
    with _patch_client(content):
        _, bucket = narrate_vice("hemingway", 500, None)
    assert bucket == "medium"


def test_uppercase_duration_normalized():
    content = json.dumps({
        "narration": "X did Y",
        "duration": "SHORT",
    })
    with _patch_client(content):
        _, bucket = narrate_vice("x", 100, None)
    assert bucket == "short"


def test_missing_duration_field_uses_default():
    content = json.dumps({"narration": "Buddha donated"})
    with _patch_client(content):
        _, bucket = narrate_vice("buddha", 200, None)
    assert bucket == "medium"


def test_empty_narration_falls_back_to_template():
    content = json.dumps({"narration": "  ", "duration": "long"})
    with _patch_client(content):
        narration, bucket = narrate_vice("buddha", 200, None)
    # Templated fallback contains the personality id and amount
    assert "buddha" in narration
    assert "$200" in narration
    assert bucket == "medium"


def test_non_json_response_falls_back_to_template():
    with _patch_client("not valid json at all"):
        narration, bucket = narrate_vice("zoidberg", 1000, None)
    assert "zoidberg" in narration
    assert "$1,000" in narration
    assert bucket == "medium"


def test_llm_failure_falls_back_to_template():
    with _patch_client_raises(RuntimeError("network down")):
        narration, bucket = narrate_vice("offline_ai", 750, None)
    assert "offline_ai" in narration
    assert "$750" in narration
    assert bucket == "medium"


def test_strips_stray_quotes_from_narration():
    content = json.dumps({
        "narration": "\"Napoleon did a thing\"",
        "duration": "short",
    })
    with _patch_client(content):
        narration, _ = narrate_vice("napoleon", 500, None)
    assert not narration.startswith('"')
    assert not narration.endswith('"')


def test_passes_personality_config_to_prompt():
    """The user prompt should include personality fields when the repo
    is provided. Verifies the LLM client is called with a message
    containing the personality's style / anchors."""
    content = json.dumps({"narration": "x", "duration": "short"})
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
            "napoleon", 2500, None,
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
