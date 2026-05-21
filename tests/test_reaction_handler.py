"""Tests for `apply_reaction` — the emoji-reaction toggle/swap handler.

Pair-axis updates flow through a real `OpponentModelManager` +
`RelationshipRepository`, so assertions are "the right axes moved"
rather than "this mock was called" — same approach as
`test_chat_relationship_dispatch.py`.
"""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

from flask_app.handlers.reaction_handler import (
    ReactionError,
    apply_reaction,
)
from poker.memory.opponent_model import OpponentModelManager
from poker.memory.relationship_events import (
    ACTOR_AXIS_SHIFTS,
    RelationshipEvent,
)
from poker.repositories.relationship_repository import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def repo(tmp_path):
    path = str(tmp_path / "rel.db")
    SchemaManager(path).ensure_schema()
    r = RelationshipRepository(path)
    yield r
    r.close()


@pytest.fixture
def opp_manager(repo):
    mgr = OpponentModelManager(relationship_repo=repo)
    mgr.register_player_id("alice", "alice_pid")
    mgr.register_player_id("batman", "batman_pid")
    return mgr


@pytest.fixture
def ai_message():
    return {
        "id": "msg-abc",
        "sender": "batman",
        "content": "I am the night.",
        "message_type": "ai",
        "reactions": {},
    }


@pytest.fixture
def game_data(opp_manager, ai_message):
    memory_manager = SimpleNamespace(
        get_opponent_model_manager=lambda: opp_manager,
        hand_count=3,
    )
    return {
        "messages": [ai_message],
        "memory_manager": memory_manager,
    }


@pytest.fixture(autouse=True)
def deterministic_rng(monkeypatch):
    """Pin the random pool roll so emoji-specific assertions are
    stable. The handler uses module-level `random.choices`; seeding
    the global RNG keeps the tests deterministic without changing
    production code paths.
    """
    random.seed(0)
    yield


class TestErrors:
    def test_unknown_message_raises_404(self, game_data):
        with pytest.raises(ReactionError) as exc:
            apply_reaction(game_data, "missing-id", "alice", "positive")
        assert exc.value.status_code == 404

    def test_non_ai_message_raises_400(self, opp_manager):
        game_data = {
            "messages": [{"id": "m1", "sender": "alice",
                          "message_type": "player", "reactions": {}}],
            "memory_manager": SimpleNamespace(
                get_opponent_model_manager=lambda: opp_manager,
                hand_count=0,
            ),
        }
        with pytest.raises(ReactionError) as exc:
            apply_reaction(game_data, "m1", "bob", "positive")
        assert exc.value.status_code == 400

    def test_invalid_sentiment_raises_400(self, game_data):
        with pytest.raises(ReactionError) as exc:
            apply_reaction(game_data, "msg-abc", "alice", "neutral")
        assert exc.value.status_code == 400


class TestApply:
    def test_first_reaction_records_emoji_and_sentiment(
        self, game_data, ai_message,
    ):
        reactions = apply_reaction(game_data, "msg-abc", "alice", "positive")
        assert "alice" in reactions
        assert reactions["alice"]["sentiment"] == "positive"
        # The emoji is one of the positive pool members.
        assert reactions["alice"]["emoji"] in {"😂", "👏", "❤️", "🔥", "👍"}
        # The handler mutates the message dict in place.
        assert ai_message["reactions"] is reactions

    def test_first_reaction_moves_axes(self, game_data, repo):
        apply_reaction(game_data, "msg-abc", "alice", "positive")
        actor_state = repo.load_raw_relationship_state(
            "alice_pid", "batman_pid",
        )
        assert actor_state is not None
        # Likability must rise for any positive roll (both
        # FRIENDLY_BANTER and COMPLIMENT have positive likability
        # shifts). Heat is non-positive (warming, not heating).
        assert actor_state.likability > 0.5
        assert actor_state.heat <= 0.0 + 1e-9

    def test_second_click_same_sentiment_removes_entry(self, game_data):
        apply_reaction(game_data, "msg-abc", "alice", "positive")
        reactions = apply_reaction(game_data, "msg-abc", "alice", "positive")
        assert "alice" not in reactions

    def test_explicit_none_removes_entry(self, game_data):
        apply_reaction(game_data, "msg-abc", "alice", "positive")
        reactions = apply_reaction(game_data, "msg-abc", "alice", None)
        assert "alice" not in reactions

    def test_swap_replaces_emoji_and_fires_new_sentiment(
        self, game_data, repo,
    ):
        # First click: positive — axis lifts likability.
        apply_reaction(game_data, "msg-abc", "alice", "positive")
        before = repo.load_raw_relationship_state("alice_pid", "batman_pid")
        assert before is not None
        before_likability = before.likability

        # Swap to negative — entry replaced, negative event fires too.
        reactions = apply_reaction(game_data, "msg-abc", "alice", "negative")
        assert reactions["alice"]["sentiment"] == "negative"
        assert reactions["alice"]["emoji"] in {"😴", "🙄", "👎", "😬", "😠"}

        after = repo.load_raw_relationship_state("alice_pid", "batman_pid")
        # The negative shift (TRASH_TALK) adds heat and pulls
        # likability down compared to the post-positive snapshot.
        assert after.heat > before.heat
        assert after.likability < before_likability

    def test_remove_does_not_double_dip_axes(self, game_data, repo):
        # Clicking the same sentiment twice removes the entry but
        # must NOT fire a second axis shift on the way out (there's
        # no inverse event in the relationship layer; firing again
        # would compound the original shift).
        apply_reaction(game_data, "msg-abc", "alice", "positive")
        state_after_first = repo.load_raw_relationship_state(
            "alice_pid", "batman_pid",
        )
        apply_reaction(game_data, "msg-abc", "alice", "positive")  # remove
        state_after_remove = repo.load_raw_relationship_state(
            "alice_pid", "batman_pid",
        )
        # Axis values stay exactly where they were after the first
        # click — removal is a UI-only operation.
        assert state_after_remove.likability == pytest.approx(
            state_after_first.likability,
        )
        assert state_after_remove.heat == pytest.approx(
            state_after_first.heat,
        )


class TestDispatchSilentNoOps:
    def test_missing_memory_manager_does_not_raise(self, ai_message):
        game_data = {"messages": [ai_message]}
        # Reaction is still recorded on the message dict; the axis
        # update is skipped silently (parallel to the
        # chat_relationship.py behavior).
        reactions = apply_reaction(
            game_data, "msg-abc", "alice", "positive",
        )
        assert "alice" in reactions

    def test_self_reaction_skipped_silently(self, game_data, repo):
        # If somehow the reactor IS the AI sender (misrouted call),
        # the axis update must be skipped — same self-target guard
        # the chat dispatcher uses. The message-side reaction still
        # records (the API is permissive at that surface).
        apply_reaction(game_data, "msg-abc", "batman", "positive")
        actor_state = repo.load_raw_relationship_state(
            "batman_pid", "batman_pid",
        )
        assert actor_state is None
