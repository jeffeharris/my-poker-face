"""Unit tests for `cash_mode.leave_narrative`.

Covers prompt assembly, signal-hint selection, response rendering,
and the disabled-env passthrough. Does NOT exercise the LLM provider
— `generate_leave_comment` is tested with an injected `LLMClient`
double whose `complete()` returns a canned `LLMResponse`.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from cash_mode.leave_narrative import (
    LeaveNarrativeContext,
    _build_messages,
    _render_sequence,
    _SIGNAL_HINTS,
    clear_results,
    generate_leave_comment,
    get_leave_comment,
    is_disabled,
    queue_leave_comment,
)


def _ctx(**overrides):
    base = dict(
        personality_name="Test Player",
        play_style="balanced",
        default_attitude="calm",
        verbal_tics=("Cheers.",),
        physical_tics=("*nods*",),
        decision="take_break",
        dominant_signal="short",
        stake_label="$10",
        chips_at_exit=50,
        min_buy_in=200,
    )
    base.update(overrides)
    return LeaveNarrativeContext(**base)


class TestBuildMessages:
    def test_includes_signal_hint(self):
        msgs = _build_messages(_ctx(dominant_signal="bust"))
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert _SIGNAL_HINTS["bust"]["hint"] in msgs[1]["content"]
        assert _SIGNAL_HINTS["bust"]["tone"] in msgs[1]["content"]

    def test_unknown_signal_falls_back_to_default(self):
        msgs = _build_messages(_ctx(dominant_signal="weird_unmapped"))
        assert _SIGNAL_HINTS[""]["hint"] in msgs[1]["content"]

    def test_personality_traits_carried(self):
        msgs = _build_messages(_ctx(
            personality_name="Napoleon",
            play_style="aggressive",
            default_attitude="dominant",
            verbal_tics=("Vive la France.",),
            physical_tics=("*straightens uniform*",),
        ))
        sys_prompt = msgs[0]["content"]
        user_msg = msgs[1]["content"]
        assert "Napoleon" in sys_prompt
        assert "aggressive" in sys_prompt
        assert "dominant" in sys_prompt
        assert "Vive la France." in user_msg
        assert "*straightens uniform*" in user_msg

    def test_dramatic_sequence_guidance_in_system_prompt(self):
        # Belt-and-suspenders: regression guard if someone refactors the
        # system prompt and drops the shared guidance block.
        msgs = _build_messages(_ctx())
        assert "ACTIONS:" in msgs[0]["content"]
        assert "SPEECH:" in msgs[0]["content"]

    def test_chips_and_stake_in_user_message(self):
        msgs = _build_messages(_ctx(chips_at_exit=850, stake_label="$25"))
        assert "$850" in msgs[1]["content"]
        assert "$25" in msgs[1]["content"]


class TestRenderSequence:
    def test_joins_beats_with_space(self):
        out = _render_sequence({"dramatic_sequence": ["*nods*", "Goodbye."]})
        assert out == "*nods* Goodbye."

    def test_trims_empty_beats(self):
        out = _render_sequence({"dramatic_sequence": ["*nods*", "", "  ", "GG."]})
        assert out == "*nods* GG."

    def test_caps_at_four_beats(self):
        out = _render_sequence({"dramatic_sequence": ["a", "b", "c", "d", "e", "f"]})
        assert out == "a b c d"

    def test_none_when_no_sequence(self):
        assert _render_sequence({"other_field": "foo"}) is None
        assert _render_sequence({"dramatic_sequence": []}) is None
        assert _render_sequence({"dramatic_sequence": "not a list"}) is None
        assert _render_sequence(None) is None


class TestGenerateLeaveComment:
    def _mock_client(self, response_content: str, *, status: str = "ok"):
        client = MagicMock()
        response = MagicMock()
        response.content = response_content
        response.status = status
        client.complete.return_value = response
        return client

    def test_happy_path_returns_string(self):
        canned = json.dumps({
            "dramatic_sequence": ["*tips hat*", "Until next time."],
        })
        client = self._mock_client(canned)
        out = generate_leave_comment(_ctx(), llm_client=client)
        assert out == "*tips hat* Until next time."

    def test_tags_call_type_and_template(self):
        canned = json.dumps({"dramatic_sequence": ["bye"]})
        client = self._mock_client(canned)
        generate_leave_comment(_ctx(), llm_client=client, owner_id="user-42")
        call_kwargs = client.complete.call_args.kwargs
        # The prompt viewer filters on this template name. If anyone
        # renames it, the viewer's filter breaks — guard it.
        assert call_kwargs["prompt_template"] == "leave_narrative"
        assert call_kwargs["call_type"].value == "commentary"
        assert call_kwargs["owner_id"] == "user-42"

    def test_non_json_response_returns_none(self):
        client = self._mock_client("not even json")
        assert generate_leave_comment(_ctx(), llm_client=client) is None

    def test_empty_response_returns_none(self):
        client = self._mock_client("")
        assert generate_leave_comment(_ctx(), llm_client=client) is None

    def test_llm_exception_returns_none(self):
        client = MagicMock()
        client.complete.side_effect = RuntimeError("boom")
        assert generate_leave_comment(_ctx(), llm_client=client) is None


class TestDisabledFlag:
    def test_disabled_in_test_suite(self):
        # conftest.py sets this for the cash_mode test package — confirm
        # the gate works end-to-end so queue_leave_comment stays cheap.
        assert os.environ.get("CASH_LEAVE_NARRATIVE_DISABLED") == "1"
        assert is_disabled()

    def test_queue_is_noop_when_disabled(self):
        clear_results()
        queue_leave_comment("t1", "p1", "2026-01-01T00:00:00", _ctx())
        assert get_leave_comment("t1", "p1", "2026-01-01T00:00:00") is None
