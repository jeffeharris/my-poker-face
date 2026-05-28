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
    _JOIN_HINT,
    _SIGNAL_HINTS,
    JoinNarrativeContext,
    LeaveNarrativeContext,
    _build_join_messages,
    _build_messages,
    _render_sequence,
    clear_results,
    generate_join_comment,
    generate_leave_comment,
    get_comment,
    get_leave_comment,
    is_disabled,
    queue_join_comment,
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
        msgs = _build_messages(
            _ctx(
                personality_name="Napoleon",
                play_style="aggressive",
                default_attitude="dominant",
                verbal_tics=("Vive la France.",),
                physical_tics=("*straightens uniform*",),
            )
        )
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
    def test_joins_beats_with_newline(self):
        # Newline-joined to match the in-hand decision/comment path (so the
        # frontend renders each beat — action vs speech — distinctly).
        out = _render_sequence({"dramatic_sequence": ["*nods*", "Goodbye."]})
        assert out == "*nods*\nGoodbye."

    def test_trims_empty_beats(self):
        out = _render_sequence({"dramatic_sequence": ["*nods*", "", "  ", "GG."]})
        assert out == "*nods*\nGG."

    def test_caps_at_four_beats(self):
        out = _render_sequence({"dramatic_sequence": ["a", "b", "c", "d", "e", "f"]})
        assert out == "a\nb\nc\nd"

    def test_splits_mixed_action_speech_beat(self):
        # The bug this fix targets: a beat mixing an action and speech used to
        # come back as one run-on line. normalize_dramatic_sequence splits it.
        out = _render_sequence({"dramatic_sequence": ["*tosses chips* good game"]})
        assert out == "*tosses chips*\ngood game"

    def test_renders_bare_string(self):
        # The LLM occasionally returns a string instead of a list; render it
        # (same as the decision path) rather than dropping it.
        assert _render_sequence({"dramatic_sequence": "See you around."}) == "See you around."

    def test_none_when_no_sequence(self):
        assert _render_sequence({"other_field": "foo"}) is None
        assert _render_sequence({"dramatic_sequence": []}) is None
        assert _render_sequence({"dramatic_sequence": 42}) is None
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
        canned = json.dumps(
            {
                "dramatic_sequence": ["*tips hat*", "Until next time."],
            }
        )
        client = self._mock_client(canned)
        out = generate_leave_comment(_ctx(), llm_client=client)
        assert out == "*tips hat*\nUntil next time."

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


# --- Join narrative ------------------------------------------------------------


def _jctx(**overrides):
    base = dict(
        personality_name="Test Player",
        play_style="balanced",
        default_attitude="calm",
        verbal_tics=("Cheers.",),
        physical_tics=("*nods*",),
        stake_label="$10",
        chips_at_sit=200,
        min_buy_in=200,
    )
    base.update(overrides)
    return JoinNarrativeContext(**base)


class TestBuildJoinMessages:
    def test_uses_arrival_hint(self):
        msgs = _build_join_messages(_jctx())
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert _JOIN_HINT["hint"] in msgs[1]["content"]
        assert _JOIN_HINT["tone"] in msgs[1]["content"]
        # Sanity: the system prompt frames this as arrival, not exit.
        assert "sitting down" in msgs[0]["content"].lower()

    def test_personality_traits_carried(self):
        msgs = _build_join_messages(
            _jctx(
                personality_name="Napoleon",
                play_style="aggressive",
                default_attitude="dominant",
                verbal_tics=("Vive la France.",),
                physical_tics=("*straightens uniform*",),
            )
        )
        sys_prompt = msgs[0]["content"]
        user_msg = msgs[1]["content"]
        assert "Napoleon" in sys_prompt
        assert "aggressive" in sys_prompt
        assert "Vive la France." in user_msg
        assert "*straightens uniform*" in user_msg

    def test_chips_and_stake_in_user_message(self):
        msgs = _build_join_messages(_jctx(chips_at_sit=500, stake_label="$25"))
        assert "$500" in msgs[1]["content"]
        assert "$25" in msgs[1]["content"]


class TestGenerateJoinComment:
    def _mock_client(self, response_content: str):
        client = MagicMock()
        response = MagicMock()
        response.content = response_content
        response.status = "ok"
        client.complete.return_value = response
        return client

    def test_happy_path_returns_string(self):
        canned = json.dumps(
            {
                "dramatic_sequence": ["*pulls up a chair*", "Evening, folks."],
            }
        )
        client = self._mock_client(canned)
        out = generate_join_comment(_jctx(), llm_client=client)
        assert out == "*pulls up a chair*\nEvening, folks."

    def test_tags_join_template(self):
        canned = json.dumps({"dramatic_sequence": ["hi"]})
        client = self._mock_client(canned)
        generate_join_comment(_jctx(), llm_client=client, owner_id="user-99")
        call_kwargs = client.complete.call_args.kwargs
        # Distinct template so the prompt viewer separates arrivals
        # from exits — guard against accidental rename.
        assert call_kwargs["prompt_template"] == "join_narrative"
        assert call_kwargs["call_type"].value == "commentary"
        assert call_kwargs["owner_id"] == "user-99"

    def test_non_json_returns_none(self):
        client = self._mock_client("not json")
        assert generate_join_comment(_jctx(), llm_client=client) is None


class TestQueueJoinNoopWhenDisabled:
    def test_queue_join_is_noop_when_disabled(self):
        clear_results()
        queue_join_comment("t2", "p2", "2026-01-01T00:00:00", _jctx())
        assert get_comment("t2", "p2", "2026-01-01T00:00:00") is None

    def test_get_comment_alias(self):
        # `get_leave_comment` is a back-compat alias — must resolve to
        # the same function so existing callers stay correct.
        assert get_leave_comment is get_comment


class TestOnCompleteCallback:
    """Worker invokes on_complete(comment) when LLM returns a comment.

    The seated-table chat path relies on this so it can send the AI's
    farewell as an in-game chat message after the LLM call settles.
    """

    def test_worker_invokes_callback(self):
        # Drive the worker function directly so we don't need to
        # toggle the disabled gate or wait on the thread pool.
        from cash_mode.leave_narrative import _worker

        captured: list = []

        def _cb(comment: str) -> None:
            captured.append(comment)

        # Patch generate_leave_comment to return a deterministic
        # rendered string so the worker has something to pass through.
        from cash_mode import leave_narrative as ln

        original = ln.generate_leave_comment
        ln.generate_leave_comment = lambda ctx, owner_id=None: "*tips hat* GG."
        try:
            _worker(("t", "p", "ts"), _ctx(), None, _cb)
        finally:
            ln.generate_leave_comment = original

        assert captured == ["*tips hat* GG."]

    def test_join_worker_invokes_callback(self):
        from cash_mode import leave_narrative as ln
        from cash_mode.leave_narrative import _join_worker

        captured: list = []
        original = ln.generate_join_comment
        ln.generate_join_comment = lambda ctx, owner_id=None: "*sits down* Evening."
        try:
            _join_worker(("t", "p", "ts"), _jctx(), None, captured.append)
        finally:
            ln.generate_join_comment = original

        assert captured == ["*sits down* Evening."]

    def test_callback_skipped_on_empty_llm_response(self):
        from cash_mode import leave_narrative as ln
        from cash_mode.leave_narrative import _worker

        captured: list = []
        original = ln.generate_leave_comment
        ln.generate_leave_comment = lambda ctx, owner_id=None: None
        try:
            _worker(("t", "p", "ts"), _ctx(), None, captured.append)
        finally:
            ln.generate_leave_comment = original

        assert captured == []

    def test_callback_exception_swallowed(self):
        # A buggy on_complete must NOT take down the worker thread —
        # the system message is already in chat and a crashed callback
        # would just lose the AI follow-up.
        from cash_mode import leave_narrative as ln
        from cash_mode.leave_narrative import _worker

        def _boom(comment: str) -> None:
            raise RuntimeError("oops")

        original = ln.generate_leave_comment
        ln.generate_leave_comment = lambda ctx, owner_id=None: "comment"
        try:
            # Should not raise.
            _worker(("t", "p", "ts"), _ctx(), None, _boom)
        finally:
            ln.generate_leave_comment = original
