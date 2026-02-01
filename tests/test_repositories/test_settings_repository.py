"""Tests for SettingsRepository."""
import pytest
from poker.repositories.settings_repository import SettingsRepository


@pytest.fixture
def repo(db_path):
    r = SettingsRepository(db_path)
    yield r
    r.close()


def test_get_setting_returns_default_when_not_set(repo):
    assert repo.get_setting("nonexistent") is None
    assert repo.get_setting("nonexistent", "fallback") == "fallback"


def test_set_and_get_setting(repo):
    assert repo.set_setting("test_key", "test_value") is True
    assert repo.get_setting("test_key") == "test_value"


def test_set_setting_with_description(repo):
    repo.set_setting("my_key", "my_val", description="A test setting")
    assert repo.get_setting("my_key") == "my_val"


def test_set_setting_upserts(repo):
    repo.set_setting("key", "v1")
    assert repo.get_setting("key") == "v1"
    repo.set_setting("key", "v2")
    assert repo.get_setting("key") == "v2"


def test_get_all_settings(repo):
    repo.set_setting("alpha", "1")
    repo.set_setting("beta", "2", description="second")

    all_settings = repo.get_all_settings()
    assert "alpha" in all_settings
    assert "beta" in all_settings
    assert all_settings["alpha"]["value"] == "1"
    assert all_settings["beta"]["value"] == "2"
    assert all_settings["beta"]["description"] == "second"


def test_get_all_settings_empty(repo):
    assert repo.get_all_settings() == {}


def test_delete_setting(repo):
    repo.set_setting("to_delete", "val")
    assert repo.delete_setting("to_delete") is True
    assert repo.get_setting("to_delete") is None


def test_delete_setting_not_found(repo):
    assert repo.delete_setting("nonexistent") is False
