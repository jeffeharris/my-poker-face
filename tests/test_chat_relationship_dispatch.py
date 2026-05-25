"""Tests for the chat-send → relationship-event dispatch.

The Flask chat-send route extracts `(tone, intensity, addressing)` from
the request body and forwards to `_dispatch_chat_relationship_event`,
which maps the tone to a `RelationshipEvent` and fires `record_event`.
These tests target the dispatch helper directly with a real
`OpponentModelManager` + `RelationshipRepository` so the assertion is
"the right axes moved by the right amount" rather than "this mock was
called."
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

from flask_app.handlers.chat_relationship import dispatch_chat_relationship_event
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
    # Register both sides so resolve_player_id returns stable ids
    # rather than falling back to display names. Either path works for
    # the relationship layer; this just keeps the test fixture mirror
    # of production setup where player_ids are registered at startup.
    mgr.register_player_id("alice", "alice_pid")
    mgr.register_player_id("bob", "bob_pid")
    return mgr


@pytest.fixture
def game_data(opp_manager):
    # Minimal shape: just the manager hook the dispatch helper needs.
    memory_manager = SimpleNamespace(
        get_opponent_model_manager=lambda: opp_manager,
    )
    return {"memory_manager": memory_manager}


class TestDispatchSkipsWhenInputMissing:
    def test_no_tone_skips_dispatch(self, game_data, repo):
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone=None,
            intensity=None,
        )
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_no_addressing_skips_dispatch(self, game_data, repo):
        # Table-broadcast: no specific target, so no relationship event.
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            None,
            tone="goad",
            intensity="spicy",
        )
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_empty_addressing_skips_dispatch(self, game_data, repo):
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            [],
            tone="goad",
            intensity="spicy",
        )
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_bluff_tone_skips_dispatch(self, game_data, repo):
        # bluff is the documented no-op tone (about speaker's own hand).
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="bluff",
            intensity="spicy",
        )
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_unknown_tone_skips_dispatch(self, game_data, repo):
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="snarky",
            intensity="spicy",
        )
        assert repo.load_raw_relationship_state("alice_pid", "bob_pid") is None

    def test_no_memory_manager_skips_dispatch(self, repo):
        dispatch_chat_relationship_event(
            {},
            "alice",
            ["bob"],
            tone="goad",
            intensity="spicy",
        )
        # No assertion needed beyond "didn't raise" — there's no repo
        # to inspect in this game_data shape.


class TestDispatchFiresEvent:
    def test_spicy_goad_applies_full_trash_talk_shift(
        self,
        game_data,
        repo,
    ):
        # spicy + goad → TRASH_TALK at multiplier 1.0.
        # Actor (alice) shift from the dispatch table: heat +0.10,
        # likability -0.05. The bilateral update also writes the
        # mirror row.
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="goad",
            intensity="spicy",
        )
        actor_state = repo.load_raw_relationship_state("alice_pid", "bob_pid")
        assert actor_state is not None
        expected = ACTOR_AXIS_SHIFTS[RelationshipEvent.TRASH_TALK]
        # Default RelationshipState starts at heat=0, respect=0.5,
        # likability=0.5; the shift is applied on top.
        assert actor_state.heat == pytest.approx(expected.heat)
        assert actor_state.respect == pytest.approx(0.5 + expected.respect)
        assert actor_state.likability == pytest.approx(0.5 + expected.likability)

    def test_chill_needle_compounds_to_quarter_shift(
        self,
        game_data,
        repo,
    ):
        # needle base = 0.5, chill modifier = 0.5 → composed multiplier
        # 0.25. The applied TRASH_TALK actor shift is scaled accordingly.
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="needle",
            intensity="chill",
        )
        actor_state = repo.load_raw_relationship_state("alice_pid", "bob_pid")
        assert actor_state is not None
        expected = ACTOR_AXIS_SHIFTS[RelationshipEvent.TRASH_TALK]
        assert actor_state.heat == pytest.approx(expected.heat * 0.25)
        assert actor_state.respect == pytest.approx(0.5 + expected.respect * 0.25)
        assert actor_state.likability == pytest.approx(0.5 + expected.likability * 0.25)

    def test_gloat_applies_taunt_post_win(self, game_data, repo):
        # Post-round tone; intensity is ignored.
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="gloat",
            intensity=None,
        )
        actor_state = repo.load_raw_relationship_state("alice_pid", "bob_pid")
        assert actor_state is not None
        expected = ACTOR_AXIS_SHIFTS[RelationshipEvent.TAUNT_POST_WIN]
        assert actor_state.heat == pytest.approx(expected.heat)
        assert actor_state.respect == pytest.approx(0.5 + expected.respect)
        assert actor_state.likability == pytest.approx(0.5 + expected.likability)

    def test_memorable_hand_attached_when_hand_count_present(
        self,
        opp_manager,
        repo,
    ):
        """When the chat path is invoked during an active hand, the
        bilateral axis update should attach a MemorableHand sidecar
        on the actor's in-memory PlayerModel — same surface
        hand-outcome events use. Without this, chat-driven movement
        is invisible in the debug view (axes shift but no narrative).
        """
        from types import SimpleNamespace

        memory_manager = SimpleNamespace(
            get_opponent_model_manager=lambda: opp_manager,
            hand_count=7,
        )
        game_data = {"memory_manager": memory_manager}

        # Pre-create the model so add_memorable_hand has a target to
        # attach to. (Production path creates models on register +
        # first interaction; the test fixture does this explicitly.)
        opp_manager.get_model("alice", "bob")

        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["bob"],
            tone="goad",
            intensity="spicy",
        )
        model = opp_manager.get_model("alice", "bob")
        assert len(model.memorable_hands) == 1
        assert model.memorable_hands[0].hand_id == 7
        assert "alice → bob" in model.memorable_hands[0].narrative

    def test_self_targeted_message_is_silently_skipped(
        self,
        game_data,
        repo,
    ):
        # actor_id == target_id should never fire. The route is meant
        # to be human-to-AI but a misrouted self-addressed message
        # shouldn't crash and shouldn't write any state.
        dispatch_chat_relationship_event(
            game_data,
            "alice",
            ["alice"],
            tone="goad",
            intensity="spicy",
        )
        assert repo.load_raw_relationship_state("alice_pid", "alice_pid") is None
