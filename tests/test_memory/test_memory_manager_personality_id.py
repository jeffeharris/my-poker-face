"""Tests for personality_id wiring through AIMemoryManager.

initialize_for_player / initialize_human_observer accept an optional
personality_id and register it with the opponent_model_manager. This
test file covers the contract — game-startup callers can pass whatever
they resolve from PersonalityRepository.resolve_name_to_personality_id
and the manager state ends up correct.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def mgr():
    from poker.memory.memory_manager import AIMemoryManager
    # No persistence — tests just exercise in-memory state.
    return AIMemoryManager(game_id="test_game", db_path=None)


class TestRegisterPlayerIdOnInitialize:
    def test_ai_player_id_registered(self, mgr):
        mgr.initialize_for_player("Bob Ross", personality_id="bob_ross")
        assert mgr.opponent_model_manager._name_to_id["Bob Ross"] == "bob_ross"

    def test_human_player_can_have_none_id(self, mgr):
        mgr.initialize_human_observer("Jeff", personality_id=None)
        # None is explicitly registered (different from "not registered")
        assert "Jeff" in mgr.opponent_model_manager._name_to_id
        assert mgr.opponent_model_manager._name_to_id["Jeff"] is None

    def test_default_arg_is_none(self, mgr):
        """Pre-existing callers that don't pass personality_id should
        still work — the parameter defaults to None and the registry
        gets a None entry, matching the human-observer semantics."""
        mgr.initialize_for_player("Mystery Bot")
        assert mgr.opponent_model_manager._name_to_id["Mystery Bot"] is None

    def test_subsequent_get_model_uses_registered_id(self, mgr):
        mgr.initialize_for_player("Alice", personality_id="alice")
        mgr.initialize_for_player("Bob", personality_id="bob")

        m = mgr.opponent_model_manager.get_model("Alice", "Bob")
        assert m.observer_id == "alice"
        assert m.opponent_id == "bob"

    def test_double_initialize_is_idempotent(self, mgr):
        """initialize_for_player short-circuits if already initialized.
        The personality_id registration on the second call is a no-op
        (the first registration sticks)."""
        mgr.initialize_for_player("Bob", personality_id="first_id")
        mgr.initialize_for_player("Bob", personality_id="different_id")

        # First-registered id is what's stored
        assert mgr.opponent_model_manager._name_to_id["Bob"] == "first_id"

    def test_mixed_human_and_ai_table(self, mgr):
        """Game startup loop: register all seats, regardless of type."""
        mgr.initialize_for_player("Bob Ross", personality_id="bob_ross")
        mgr.initialize_for_player("Abraham Lincoln", personality_id="abraham_lincoln")
        mgr.initialize_human_observer("Jeff", personality_id=None)

        reg = mgr.opponent_model_manager._name_to_id
        assert reg["Bob Ross"] == "bob_ross"
        assert reg["Abraham Lincoln"] == "abraham_lincoln"
        assert reg["Jeff"] is None

        # Each player can observe every other; ids are populated where known
        m_bob_obs_jeff = mgr.opponent_model_manager.get_model("Bob Ross", "Jeff")
        assert m_bob_obs_jeff.observer_id == "bob_ross"
        assert m_bob_obs_jeff.opponent_id is None  # Jeff is human

        m_jeff_obs_bob = mgr.opponent_model_manager.get_model("Jeff", "Bob Ross")
        assert m_jeff_obs_bob.observer_id is None
        assert m_jeff_obs_bob.opponent_id == "bob_ross"
