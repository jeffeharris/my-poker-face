"""Tests for the recent-reactions prompt summarizer.

Verifies the formatting and filtering rules without coupling to any
controller — the helper takes a raw messages list (the same shape
`game_data['messages']` carries) and an AI name, then returns a
prompt-ready block.
"""

from __future__ import annotations

import pytest

from poker.memory.reaction_prompt import summarize_recent_reactions


def _ai_msg(msg_id: str, sender: str, content: str, reactions=None) -> dict:
    return {
        "id": msg_id,
        "sender": sender,
        "content": content,
        "message_type": "ai",
        "reactions": reactions or {},
    }


def _player_msg(sender: str, content: str) -> dict:
    return {
        "id": "p-" + sender,
        "sender": sender,
        "content": content,
        "message_type": "player",
        "reactions": {},
    }


class TestEmptyCases:
    def test_no_messages_returns_empty(self):
        assert summarize_recent_reactions([], "batman") == ""

    def test_no_ai_name_returns_empty(self):
        msgs = [_ai_msg("m1", "batman", "hi", {"alice": {"emoji": "❤️", "sentiment": "positive"}})]
        assert summarize_recent_reactions(msgs, "") == ""

    def test_no_ai_messages_returns_empty(self):
        msgs = [_player_msg("alice", "hi")]
        assert summarize_recent_reactions(msgs, "batman") == ""

    def test_ai_messages_without_reactions_return_empty(self):
        msgs = [_ai_msg("m1", "batman", "I am the night.")]
        assert summarize_recent_reactions(msgs, "batman") == ""

    def test_reactions_on_other_ai_messages_ignored(self):
        # Reactions on Joker's comment shouldn't surface in Batman's
        # context — only the speaker's own outgoing messages matter.
        msgs = [
            _ai_msg("m1", "joker", "Why so serious?",
                    {"alice": {"emoji": "😂", "sentiment": "positive"}}),
        ]
        assert summarize_recent_reactions(msgs, "batman") == ""


class TestFormatting:
    def test_single_reaction_renders_with_emoji_and_snippet(self):
        msgs = [
            _ai_msg("m1", "batman", "I am the night.",
                    {"alice": {"emoji": "❤️", "sentiment": "positive"}}),
        ]
        out = summarize_recent_reactions(msgs, "batman")
        assert out.startswith("RECENT REACTIONS TO YOUR COMMENTS:")
        assert "I am the night." in out
        assert "alice ❤️" in out

    def test_multiple_reactors_concatenated(self):
        msgs = [
            _ai_msg("m1", "batman", "I am the night.",
                    {
                        "alice": {"emoji": "❤️", "sentiment": "positive"},
                        "bob":   {"emoji": "😴", "sentiment": "negative"},
                    }),
        ]
        out = summarize_recent_reactions(msgs, "batman")
        assert "alice ❤️" in out
        assert "bob 😴" in out

    def test_long_snippet_truncated(self):
        long = "I am the night, and shadow, and whispered fear in the corners of every alleyway."
        msgs = [
            _ai_msg("m1", "batman", long,
                    {"alice": {"emoji": "❤️", "sentiment": "positive"}}),
        ]
        out = summarize_recent_reactions(msgs, "batman")
        # Truncated to 57 chars + "..." — not full length.
        assert "..." in out
        assert long not in out


class TestLookback:
    def test_lookback_caps_messages_considered(self):
        # Three AI messages from batman, all reacted to. Lookback=2
        # should surface only the two most-recent (latest message
        # is at the end of the list).
        msgs = [
            _ai_msg("m1", "batman", "first",
                    {"alice": {"emoji": "❤️", "sentiment": "positive"}}),
            _ai_msg("m2", "batman", "second",
                    {"bob": {"emoji": "😂", "sentiment": "positive"}}),
            _ai_msg("m3", "batman", "third",
                    {"carol": {"emoji": "🔥", "sentiment": "positive"}}),
        ]
        out = summarize_recent_reactions(msgs, "batman", lookback=2)
        assert "second" in out
        assert "third" in out
        assert "first" not in out  # oldest dropped

    def test_lookback_of_one_takes_only_latest(self):
        msgs = [
            _ai_msg("m1", "batman", "older",
                    {"alice": {"emoji": "❤️", "sentiment": "positive"}}),
            _ai_msg("m2", "batman", "newest",
                    {"bob": {"emoji": "😂", "sentiment": "positive"}}),
        ]
        out = summarize_recent_reactions(msgs, "batman", lookback=1)
        assert "newest" in out
        assert "older" not in out

    def test_player_messages_interleaved_dont_count_toward_lookback(self):
        # Lookback counts AI-from-speaker messages only — interleaved
        # player chats shouldn't push the AI's reacted comments out
        # of the window.
        msgs = [
            _ai_msg("m1", "batman", "first",
                    {"alice": {"emoji": "❤️", "sentiment": "positive"}}),
            _player_msg("alice", "noise 1"),
            _player_msg("bob", "noise 2"),
            _ai_msg("m2", "batman", "second",
                    {"bob": {"emoji": "😂", "sentiment": "positive"}}),
        ]
        out = summarize_recent_reactions(msgs, "batman", lookback=2)
        # Both AI messages surface — player noise doesn't count.
        assert "first" in out
        assert "second" in out


class TestRobustness:
    def test_malformed_reaction_record_skipped(self):
        # A reaction record missing the 'emoji' field shouldn't crash;
        # the reactor's entry is silently skipped.
        msgs = [
            _ai_msg("m1", "batman", "comment",
                    {
                        "alice": {"sentiment": "positive"},  # missing emoji
                        "bob":   {"emoji": "😂", "sentiment": "positive"},
                    }),
        ]
        out = summarize_recent_reactions(msgs, "batman")
        assert "bob 😂" in out
        # Alice's malformed entry is dropped.
        assert "alice" not in out

    def test_non_dict_reactions_field_handled(self):
        # Defensive: if `reactions` ever arrives as a list (legacy
        # data, bad serializer), don't blow up.
        msgs = [
            _ai_msg("m1", "batman", "comment"),
        ]
        msgs[0]["reactions"] = ["not", "a", "dict"]  # malformed
        assert summarize_recent_reactions(msgs, "batman") == ""
