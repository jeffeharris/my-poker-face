"""Tests for PromptPresetRepository."""
import pytest
from poker.repositories.prompt_preset_repository import PromptPresetRepository


@pytest.fixture
def repo(db_path):
    r = PromptPresetRepository(db_path)
    yield r
    r.close()


class TestPromptPresets:
    def test_create_and_get_preset(self, repo):
        pid = repo.create_prompt_preset(
            name='test-preset',
            description='A test preset',
            prompt_config={'hand_analysis': True},
            guidance_injection='Be extra careful',
        )
        assert pid > 0

        loaded = repo.get_prompt_preset(pid)
        assert loaded is not None
        assert loaded['name'] == 'test-preset'
        assert loaded['prompt_config'] == {'hand_analysis': True}
        assert loaded['guidance_injection'] == 'Be extra careful'

    def test_get_preset_not_found(self, repo):
        assert repo.get_prompt_preset(9999) is None

    def test_get_preset_by_name(self, repo):
        repo.create_prompt_preset(name='named-preset')
        loaded = repo.get_prompt_preset_by_name('named-preset')
        assert loaded is not None
        assert loaded['name'] == 'named-preset'

    def test_list_presets(self, repo):
        # Count existing system presets first
        existing = repo.list_prompt_presets()
        existing_count = len(existing)

        repo.create_prompt_preset(name='preset-1')
        repo.create_prompt_preset(name='preset-2')

        presets = repo.list_prompt_presets()
        assert len(presets) == existing_count + 2

    def test_update_preset(self, repo):
        pid = repo.create_prompt_preset(name='orig')
        updated = repo.update_prompt_preset(pid, name='renamed', description='updated desc')
        assert updated is True

        loaded = repo.get_prompt_preset(pid)
        assert loaded['name'] == 'renamed'
        assert loaded['description'] == 'updated desc'

    def test_update_preset_not_found(self, repo):
        assert repo.update_prompt_preset(9999, name='nope') is False

    def test_update_preset_for_owner(self, repo):
        pid = repo.create_prompt_preset(name='owner-orig', owner_id='user-1')
        updated = repo.update_prompt_preset_for_owner(pid, 'user-1', name='owner-renamed')
        assert updated is True

        loaded = repo.get_prompt_preset(pid)
        assert loaded['name'] == 'owner-renamed'

    def test_update_preset_for_owner_denied(self, repo):
        pid = repo.create_prompt_preset(name='owner-only', owner_id='user-1')
        updated = repo.update_prompt_preset_for_owner(pid, 'user-2', name='hijacked')
        assert updated is False

        loaded = repo.get_prompt_preset(pid)
        assert loaded['name'] == 'owner-only'

    def test_delete_preset(self, repo):
        pid = repo.create_prompt_preset(name='to-delete')
        assert repo.delete_prompt_preset(pid) is True
        assert repo.get_prompt_preset(pid) is None

    def test_delete_preset_for_owner(self, repo):
        pid = repo.create_prompt_preset(name='owner-delete', owner_id='user-1')
        assert repo.delete_prompt_preset_for_owner(pid, 'user-1') is True
        assert repo.get_prompt_preset(pid) is None

    def test_delete_preset_for_owner_denied(self, repo):
        pid = repo.create_prompt_preset(name='owner-protected', owner_id='user-1')
        assert repo.delete_prompt_preset_for_owner(pid, 'user-2') is False
        assert repo.get_prompt_preset(pid) is not None

    def test_delete_preset_not_found(self, repo):
        assert repo.delete_prompt_preset(9999) is False

    def test_duplicate_name_raises(self, repo):
        repo.create_prompt_preset(name='unique')
        with pytest.raises(ValueError, match="already exists"):
            repo.create_prompt_preset(name='unique')
