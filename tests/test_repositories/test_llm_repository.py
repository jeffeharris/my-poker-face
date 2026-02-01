"""Tests for LLMRepository."""
import sqlite3
import pytest
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.llm_repository import LLMRepository


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    SchemaManager(db_path).ensure_schema()
    r = LLMRepository(db_path)
    yield r
    r.close()


def _seed_models(repo):
    """Clear existing models and insert test models."""
    with repo._get_connection() as conn:
        conn.execute("DELETE FROM enabled_models")
        conn.execute("""
            INSERT INTO enabled_models (provider, model, enabled, sort_order)
            VALUES ('openai', 'gpt-4o', 1, 1)
        """)
        conn.execute("""
            INSERT INTO enabled_models (provider, model, enabled, sort_order)
            VALUES ('openai', 'gpt-5-nano', 0, 2)
        """)
        conn.execute("""
            INSERT INTO enabled_models (provider, model, enabled, sort_order)
            VALUES ('anthropic', 'claude-3-sonnet', 1, 1)
        """)


class TestGetAvailableProviders:
    def test_returns_all_providers(self, repo):
        _seed_models(repo)
        providers = repo.get_available_providers()
        assert 'openai' in providers
        assert 'anthropic' in providers

    def test_empty_when_no_models(self, repo):
        # The schema may seed some default models; clear them
        with repo._get_connection() as conn:
            conn.execute("DELETE FROM enabled_models")
        assert repo.get_available_providers() == set()


class TestGetEnabledModels:
    def test_returns_only_enabled_grouped_by_provider(self, repo):
        # Clear any seeded models first
        with repo._get_connection() as conn:
            conn.execute("DELETE FROM enabled_models")
        _seed_models(repo)

        enabled = repo.get_enabled_models()
        assert 'openai' in enabled
        assert 'gpt-4o' in enabled['openai']
        assert 'gpt-5-nano' not in enabled['openai']  # disabled
        assert 'anthropic' in enabled
        assert 'claude-3-sonnet' in enabled['anthropic']


class TestGetAllEnabledModels:
    def test_returns_all_models_with_metadata(self, repo):
        with repo._get_connection() as conn:
            conn.execute("DELETE FROM enabled_models")
        _seed_models(repo)

        all_models = repo.get_all_enabled_models()
        assert len(all_models) == 3
        # Check keys exist
        for m in all_models:
            assert 'id' in m
            assert 'provider' in m
            assert 'model' in m
            assert 'enabled' in m


class TestUpdateModelEnabled:
    def test_toggle_enabled(self, repo):
        with repo._get_connection() as conn:
            conn.execute("DELETE FROM enabled_models")
        _seed_models(repo)

        all_models = repo.get_all_enabled_models()
        disabled_model = next(m for m in all_models if m['model'] == 'gpt-5-nano')
        assert disabled_model['enabled'] == 0

        result = repo.update_model_enabled(disabled_model['id'], True)
        assert result is True

        updated = repo.get_all_enabled_models()
        model = next(m for m in updated if m['model'] == 'gpt-5-nano')
        assert model['enabled'] == 1

    def test_update_nonexistent_returns_false(self, repo):
        assert repo.update_model_enabled(99999, True) is False


class TestUpdateModelDetails:
    def test_update_display_name(self, repo):
        with repo._get_connection() as conn:
            conn.execute("DELETE FROM enabled_models")
        _seed_models(repo)

        all_models = repo.get_all_enabled_models()
        model = all_models[0]

        result = repo.update_model_details(model['id'], display_name='Custom Name')
        assert result is True

        updated = repo.get_all_enabled_models()
        m = next(x for x in updated if x['id'] == model['id'])
        assert m['display_name'] == 'Custom Name'

    def test_update_nonexistent_returns_false(self, repo):
        assert repo.update_model_details(99999, display_name='X') is False
