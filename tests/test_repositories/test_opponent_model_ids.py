"""Tests for personality_id persistence on opponent_models.

Covers the save / load round-trip of observer_id + opponent_id (v86)
through GameRepository.save_opponent_models / load_opponent_models.
Verifies that the in-memory OpponentModelManager (with registered ids)
survives a round-trip cleanly and that legacy rows without ids load
gracefully.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def shared_db_path(tmp_path_factory):
    """Module-scoped DB to avoid rebuilding the schema 8 times (running
    all 86 migrations per test exhausts the backend container's
    memory)."""
    from poker.repositories.schema_manager import SchemaManager

    path = str(tmp_path_factory.mktemp("opp_model_ids") / "test.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repo(shared_db_path):
    """Per-test repo with clean opponent_models / games rows."""
    import sqlite3

    from poker.repositories.game_repository import GameRepository

    # Wipe between tests — fixture scope is per-test but the DB is shared
    conn = sqlite3.connect(shared_db_path)
    conn.execute("DELETE FROM opponent_models")
    conn.execute("DELETE FROM memorable_hands")
    conn.execute("DELETE FROM games")
    conn.commit()
    conn.close()
    r = GameRepository(shared_db_path)
    yield r
    r.close()


@pytest.fixture
def game_id(shared_db_path):
    """A game_id string. opponent_models has no FK constraint on games,
    so a stub games row is just defensive in case one's added later."""
    import sqlite3

    gid = "test_game_opp_ids"
    conn = sqlite3.connect(shared_db_path)
    conn.execute(
        "INSERT OR IGNORE INTO games (game_id, game_state_json) VALUES (?, ?)",
        (gid, "{}"),
    )
    conn.commit()
    conn.close()
    return gid


def _seed_opponent_models(db_path: str, game_id: str, rows):
    """Insert rows directly into opponent_models for legacy-row scenarios."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    for observer_name, opponent_name, observer_id, opponent_id in rows:
        conn.execute(
            "INSERT INTO opponent_models "
            "(game_id, observer_name, opponent_name, observer_id, opponent_id, "
            "hands_observed) VALUES (?, ?, ?, ?, ?, 5)",
            (game_id, observer_name, opponent_name, observer_id, opponent_id),
        )
    conn.commit()
    conn.close()


class TestSaveOpponentModelsWithIds:
    def test_save_writes_observer_and_opponent_ids(self, repo, game_id, shared_db_path):
        from poker.memory.opponent_model import OpponentModelManager

        mgr = OpponentModelManager()
        mgr.register_player_id("Alice", "alice_id")
        mgr.register_player_id("Bob", "bob_id")
        mgr.get_model("Alice", "Bob")

        repo.save_opponent_models(game_id, mgr)

        # Verify direct DB state
        import sqlite3

        conn = sqlite3.connect(shared_db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT observer_id, opponent_id FROM opponent_models WHERE game_id = ?",
            (game_id,),
        ).fetchone()
        conn.close()
        assert row["observer_id"] == "alice_id"
        assert row["opponent_id"] == "bob_id"

    def test_save_falls_back_to_name_registry_for_models_without_ids(
        self, repo, game_id, shared_db_path
    ):
        """If a model is in the manager without per-row ids set but the
        manager has the name in its name_to_id registry, save_opponent_models
        should still write the ids."""
        from poker.memory.opponent_model import OpponentModel, OpponentModelManager

        mgr = OpponentModelManager()
        # Create model first, register id second — model itself has no
        # ids on it, but the registry knows them.
        mgr.models.setdefault("Alice", {})["Bob"] = OpponentModel("Alice", "Bob")
        mgr._name_to_id["Alice"] = "alice_id"
        mgr._name_to_id["Bob"] = "bob_id"

        repo.save_opponent_models(game_id, mgr)

        import sqlite3

        conn = sqlite3.connect(shared_db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT observer_id, opponent_id FROM opponent_models WHERE game_id = ?",
            (game_id,),
        ).fetchone()
        conn.close()
        assert row["observer_id"] == "alice_id"
        assert row["opponent_id"] == "bob_id"


class TestLoadOpponentModelsWithIds:
    def test_load_returns_ids_in_model_data(self, repo, game_id, shared_db_path):
        _seed_opponent_models(
            shared_db_path,
            game_id,
            [
                ("Alice", "Bob", "alice_id", "bob_id"),
            ],
        )
        result = repo.load_opponent_models(game_id)
        assert result["Alice"]["Bob"]["observer_id"] == "alice_id"
        assert result["Alice"]["Bob"]["opponent_id"] == "bob_id"

    def test_load_returns_none_ids_for_legacy_rows(self, repo, game_id, shared_db_path):
        _seed_opponent_models(
            shared_db_path,
            game_id,
            [
                ("Guest_42", "Bob", None, None),
            ],
        )
        result = repo.load_opponent_models(game_id)
        assert result["Guest_42"]["Bob"]["observer_id"] is None
        assert result["Guest_42"]["Bob"]["opponent_id"] is None

    def test_load_builds_name_to_id_sidecar(self, repo, game_id, shared_db_path):
        _seed_opponent_models(
            shared_db_path,
            game_id,
            [
                ("Alice", "Bob", "alice_id", "bob_id"),
                ("Carol", "Bob", "carol_id", "bob_id"),
            ],
        )
        result = repo.load_opponent_models(game_id)
        assert "__name_to_id__" in result
        sidecar = result["__name_to_id__"]
        assert sidecar["Alice"] == "alice_id"
        assert sidecar["Bob"] == "bob_id"
        assert sidecar["Carol"] == "carol_id"

    def test_load_omits_sidecar_when_no_ids_present(self, repo, game_id, shared_db_path):
        _seed_opponent_models(
            shared_db_path,
            game_id,
            [
                ("Guest_A", "Guest_B", None, None),
            ],
        )
        result = repo.load_opponent_models(game_id)
        # No ids → no sidecar
        assert "__name_to_id__" not in result


class TestEndToEndManagerRoundTrip:
    def test_manager_save_load_preserves_registry_and_ids(self, repo, game_id):
        from poker.memory.opponent_model import OpponentModelManager

        mgr = OpponentModelManager()
        mgr.register_player_id("Alice", "alice_id")
        mgr.register_player_id("Bob", "bob_id")
        mgr.register_player_id("Carol", None)  # human guest
        mgr.get_model("Alice", "Bob")
        mgr.get_model("Bob", "Alice")
        mgr.get_model("Alice", "Carol")

        repo.save_opponent_models(game_id, mgr)
        loaded = repo.load_opponent_models(game_id)
        restored = OpponentModelManager.from_dict(loaded)

        # Per-row ids preserved
        ab = restored.get_model("Alice", "Bob")
        assert ab.observer_id == "alice_id"
        assert ab.opponent_id == "bob_id"

        # Registry has Alice and Bob (both had non-None ids)
        assert restored._name_to_id.get("Alice") == "alice_id"
        assert restored._name_to_id.get("Bob") == "bob_id"

        # Carol's id is None and didn't appear in any row's id slot, so
        # she's absent from the rebuilt registry. That's expected — the
        # sidecar is reconstructed from observed ids on save, not from
        # an explicit serialized registry. None-registered players
        # without a corresponding personality_id are indistinguishable
        # from never-registered ones once round-tripped through the DB.
        # The relationship-layer / cash-mode work that follows is
        # responsible for re-registering active-game player ids at
        # startup anyway.
