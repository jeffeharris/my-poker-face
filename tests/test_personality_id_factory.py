"""Tests for the personality_id flow through the factory (PersonalityGenerator)
and the create_personality HTTP route.

Both creation paths must populate personality_id at row insertion time
so callers (relationship layer, bankrolls, opponent_models) can key on
the stable id from the moment a personality exists.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# --- PersonalityGenerator factory ---


@pytest.fixture
def repo_with_v85(tmp_path):
    """A PersonalityRepository pointing at a fresh v85-shape database."""
    from poker.repositories.personality_repository import PersonalityRepository
    from poker.repositories.schema_manager import SchemaManager

    db_path = str(tmp_path / "factory.db")
    SchemaManager(db_path).ensure_schema()
    repo = PersonalityRepository(db_path)
    yield repo
    repo.close()


class TestPersonalityGeneratorAssignsId:
    def test_existing_personality_load_returns_id(self, repo_with_v85):
        """When the generator finds an existing personality in the DB,
        the returned config includes `id` (from load_personality)."""
        from poker.personality_generator import PersonalityGenerator

        repo_with_v85.save_personality("Returning Hero", {"play_style": "calm"})
        gen = PersonalityGenerator(personality_repo=repo_with_v85)
        result = gen.get_personality("Returning Hero")
        assert result["id"] == "returning_hero"

    def test_freshly_generated_personality_gets_id(self, repo_with_v85):
        """For an AI-generated personality, the generator must surface
        the id assigned during save_personality. The generator can't
        derive it itself; it must capture the return value from the
        repository call."""
        from poker.personality_generator import PersonalityGenerator

        gen = PersonalityGenerator(personality_repo=repo_with_v85)

        # Mock the internal LLM call so we don't pay a real API request
        fake_config = {
            "play_style": "test",
            "default_confidence": "level",
            "default_attitude": "neutral",
            "personality_traits": {"bluff_tendency": 0.5},
            "anchors": {"baseline_aggression": 0.5},
        }
        with patch.object(gen, "_generate_personality", return_value=fake_config):
            result = gen.get_personality("Fresh Bot")

        assert result["id"] == "fresh_bot"
        # Confirm the id is persisted in the DB, not just on the returned dict
        loaded = repo_with_v85.load_personality_by_id("fresh_bot")
        assert loaded is not None
        assert loaded["name"] == "Fresh Bot"

    def test_generator_caches_with_id(self, repo_with_v85):
        """Subsequent get_personality calls hit the cache and the cached
        copy should still include the id."""
        from poker.personality_generator import PersonalityGenerator

        gen = PersonalityGenerator(personality_repo=repo_with_v85)
        fake_config = {
            "play_style": "test",
            "default_confidence": "level",
            "default_attitude": "neutral",
            "personality_traits": {},
            "anchors": {},
        }
        with patch.object(gen, "_generate_personality", return_value=fake_config):
            first = gen.get_personality("Cached Bot")

        cached = gen.get_personality("Cached Bot")
        assert cached["id"] == first["id"] == "cached_bot"


# --- create_personality HTTP route ---


class TestCreatePersonalityRoute:
    @pytest.fixture
    def authed_client(self, monkeypatch):
        """Flask test client with a stubbed authenticated user.

        The route imports `auth_manager` via `from ..extensions import
        auth_manager` and uses it as a module-level binding inside the
        route module. So the patch has to target the route module's
        binding directly — patching `flask_app.extensions.auth_manager`
        wouldn't propagate to the already-bound name inside the route
        module."""
        from flask_app import extensions as ext
        from flask_app.routes import personality_routes as route_mod
        from flask_app.ui_web import create_app

        app = create_app()

        # Re-sync the route module's bound `personality_repo` from
        # extensions. Other tests in the same xdist worker may use
        # mock_init_persistence to point the route at a tempdb that's
        # been unlinked by the time this fixture runs. create_app()
        # above invokes init_persistence(), which refreshes
        # extensions.personality_repo to the real prod-DB repo —
        # rebinding the route's reference picks that up. (The route
        # module captures `personality_repo` by name at module load,
        # so a fresh init_persistence doesn't reach into already-bound
        # names.)
        route_mod.personality_repo = ext.personality_repo

        # Stub auth: route's auth_manager.get_current_user returns a
        # fake user dict instead of consulting Flask session.
        fake_user = {"id": "test_user_42", "name": "Tester"}
        monkeypatch.setattr(
            route_mod.auth_manager,
            "get_current_user",
            lambda: fake_user,
        )

        with app.test_client() as client:
            yield client

    def test_route_returns_personality_id(self, authed_client):
        """POST /api/personality creates a row and returns its stable id."""
        from flask_app.routes import personality_routes as route_mod

        # Ensure no prior row collides (independent of test ordering).
        route_mod.personality_repo.delete_personality("Route Test Hero")

        response = authed_client.post(
            "/api/personality",
            json={"name": "Route Test Hero", "play_style": "test"},
        )
        body = response.get_json()
        assert response.status_code == 200, body
        assert body["success"] is True
        assert body["personality_id"] == "route_test_hero"

        # cleanup
        route_mod.personality_repo.delete_personality("Route Test Hero")

    def test_route_collision_returns_409(self, authed_client):
        """Existing name returns a 409 conflict; no row inserted."""
        from flask_app.routes import personality_routes as route_mod

        route_mod.personality_repo.delete_personality("Conflict Hero")
        route_mod.personality_repo.save_personality("Conflict Hero", {"play_style": "existing"})
        try:
            response = authed_client.post(
                "/api/personality",
                json={"name": "Conflict Hero", "play_style": "duplicate"},
            )
            assert response.status_code == 409
            body = response.get_json()
            assert body["success"] is False
        finally:
            route_mod.personality_repo.delete_personality("Conflict Hero")
