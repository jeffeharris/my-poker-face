"""Tests for personality_id surface on OpponentModel + OpponentModelManager.

OpponentModel rows now carry observer_id and opponent_id alongside the
display-name observer/opponent fields. The manager exposes
register_player_id() so game-startup code can associate names with
their stable ids; subsequent get_model calls populate ids automatically.
"""

from __future__ import annotations

import pytest

from poker.memory.opponent_model import (
    OpponentModel,
    OpponentModelManager,
    OpponentTendencies,
)


class TestOpponentModelIds:
    def test_init_defaults_to_none(self):
        m = OpponentModel(observer="Alice", opponent="Bob")
        assert m.observer_id is None
        assert m.opponent_id is None

    def test_init_with_explicit_ids(self):
        m = OpponentModel(
            observer="Alice",
            opponent="Bob",
            observer_id="alice",
            opponent_id="bob",
        )
        assert m.observer_id == "alice"
        assert m.opponent_id == "bob"

    def test_to_dict_includes_ids(self):
        m = OpponentModel(
            observer="Alice",
            opponent="Bob",
            observer_id="alice_v2",
            opponent_id="bob",
        )
        d = m.to_dict()
        assert d["observer_id"] == "alice_v2"
        assert d["opponent_id"] == "bob"

    def test_to_dict_with_none_ids_serializes(self):
        m = OpponentModel(observer="Alice", opponent="Bob")
        d = m.to_dict()
        assert d["observer_id"] is None
        assert d["opponent_id"] is None

    def test_from_dict_round_trip(self):
        m = OpponentModel(
            observer="Alice",
            opponent="Bob",
            observer_id="alice",
            opponent_id="bob",
        )
        d = m.to_dict()
        restored = OpponentModel.from_dict(d)
        assert restored.observer == "Alice"
        assert restored.opponent == "Bob"
        assert restored.observer_id == "alice"
        assert restored.opponent_id == "bob"

    def test_from_dict_back_compat_without_ids(self):
        """Older snapshots predate observer_id/opponent_id. The from_dict
        path must accept them with the fields absent and default to None."""
        legacy = {
            "observer": "Alice",
            "opponent": "Bob",
            "tendencies": OpponentTendencies().to_dict(),
            "memorable_hands": [],
            "narrative_observations": [],
        }
        m = OpponentModel.from_dict(legacy)
        assert m.observer_id is None
        assert m.opponent_id is None


class TestManagerRegisterPlayerId:
    def test_get_model_uses_registered_ids(self):
        mgr = OpponentModelManager()
        mgr.register_player_id("Alice", "alice_id")
        mgr.register_player_id("Bob", "bob_id")

        m = mgr.get_model("Alice", "Bob")
        assert m.observer_id == "alice_id"
        assert m.opponent_id == "bob_id"

    def test_get_model_without_registration_leaves_ids_none(self):
        mgr = OpponentModelManager()
        m = mgr.get_model("Alice", "Bob")
        assert m.observer_id is None
        assert m.opponent_id is None

    def test_register_after_create_backfills_observer_slot(self):
        """Models created before register_player_id should be back-filled
        when their observer id becomes known. Common case: game starts
        with a guest player who later authenticates."""
        mgr = OpponentModelManager()
        mgr.get_model("Alice", "Bob")  # both ids start None
        mgr.register_player_id("Alice", "alice_id")

        m = mgr.get_model("Alice", "Bob")  # same instance returned
        assert m.observer_id == "alice_id"

    def test_register_after_create_backfills_opponent_slot_across_observers(self):
        """Registering a player who's been observed by multiple others
        should back-fill every observer's record of them."""
        mgr = OpponentModelManager()
        mgr.get_model("Alice", "Bob")
        mgr.get_model("Carol", "Bob")
        mgr.get_model("Dave", "Bob")
        mgr.register_player_id("Bob", "bob_id")

        for observer in ["Alice", "Carol", "Dave"]:
            m = mgr.get_model(observer, "Bob")
            assert m.opponent_id == "bob_id"

    def test_register_does_not_overwrite_existing_id(self):
        """If a model already has an id (e.g. registered then re-created
        from a snapshot), a subsequent register call shouldn't replace it.
        Identity is meant to be stable. This guards against accidental
        re-keying of in-memory state mid-session."""
        mgr = OpponentModelManager()
        mgr.register_player_id("Alice", "original_id")
        mgr.get_model("Alice", "Bob")
        mgr.register_player_id("Alice", "new_id")

        m = mgr.get_model("Alice", "Bob")
        # The existing model keeps its first-registered id
        assert m.observer_id == "original_id"

    def test_register_none_explicitly(self):
        """Registering None for a name marks it as 'known to not have
        a personality_id' (e.g. a human guest). Different from
        not-registered: avoids future repeated lookup attempts."""
        mgr = OpponentModelManager()
        mgr.register_player_id("Alice", None)

        m = mgr.get_model("Alice", "Bob")
        assert m.observer_id is None


class TestManagerSerializationRoundTrip:
    def test_to_dict_includes_name_to_id_sidecar(self):
        mgr = OpponentModelManager()
        mgr.register_player_id("Alice", "alice_id")
        mgr.register_player_id("Bob", "bob_id")
        mgr.get_model("Alice", "Bob")

        d = mgr.to_dict()
        assert "__name_to_id__" in d
        assert d["__name_to_id__"]["Alice"] == "alice_id"
        assert d["__name_to_id__"]["Bob"] == "bob_id"

    def test_round_trip_preserves_ids_and_registry(self):
        mgr = OpponentModelManager()
        mgr.register_player_id("Alice", "alice_id")
        mgr.register_player_id("Bob", "bob_id")
        mgr.get_model("Alice", "Bob")
        mgr.get_model("Bob", "Alice")

        restored = OpponentModelManager.from_dict(mgr.to_dict())

        # Models keep their ids
        m1 = restored.get_model("Alice", "Bob")
        assert m1.observer_id == "alice_id"
        assert m1.opponent_id == "bob_id"

        # And the registry is restored, so any subsequent get_model
        # for a new opponent picks up Alice's id automatically
        m2 = restored.get_model("Alice", "Carol")  # Carol not registered
        assert m2.observer_id == "alice_id"
        assert m2.opponent_id is None

    def test_to_dict_without_registry_omits_sidecar(self):
        """If no names have been registered, the sidecar key shouldn't
        appear — keeps the dict clean and avoids changing behavior for
        pre-existing callers that don't use the registry yet."""
        mgr = OpponentModelManager()
        mgr.get_model("Alice", "Bob")
        d = mgr.to_dict()
        assert "__name_to_id__" not in d

    def test_legacy_dict_without_sidecar_still_loads(self):
        """Snapshots predating the sidecar should restore cleanly with
        an empty name-to-id registry."""
        legacy = {
            "Alice": {
                "Bob": OpponentModel(observer="Alice", opponent="Bob").to_dict(),
            }
        }
        mgr = OpponentModelManager.from_dict(legacy)
        assert mgr._name_to_id == {}
        m = mgr.get_model("Alice", "Bob")
        assert m.observer == "Alice"
        assert m.observer_id is None
