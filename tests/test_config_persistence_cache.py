"""Tests for T2-05: Cached SettingsRepository in config getters.

The canonical source of these getters is now core.llm.settings.
flask_app.config re-exports them for backwards compatibility.
"""

from unittest.mock import patch, MagicMock


class TestConfigPersistenceCaching:
    """Verify core.llm.settings config getters share a single SettingsRepository instance."""

    def test_config_getters_construct_settings_repo_once(self):
        """Calling multiple config getters should only construct SettingsRepository once."""
        from core.llm.settings import _get_config_persistence
        _get_config_persistence.cache_clear()

        mock_settings_repo = MagicMock()
        mock_settings_repo.get_setting.return_value = ''

        with patch('poker.repositories.SchemaManager'), \
             patch('poker.repositories.SettingsRepository', return_value=mock_settings_repo) as mock_cls:
            _get_config_persistence.cache_clear()

            from core.llm.settings import (
                get_default_provider,
                get_default_model,
                get_assistant_provider,
                get_assistant_model,
            )

            # Call all four getters
            get_default_provider()
            get_default_model()
            get_assistant_provider()
            get_assistant_model()

            # SettingsRepository should be constructed exactly once
            assert mock_cls.call_count == 1

        _get_config_persistence.cache_clear()

    def test_config_getters_share_same_instance(self):
        """All config getters should use the exact same SettingsRepository object."""
        from core.llm.settings import _get_config_persistence
        _get_config_persistence.cache_clear()

        mock_settings_repo = MagicMock()
        mock_settings_repo.get_setting.return_value = ''

        with patch('poker.repositories.SchemaManager'), \
             patch('poker.repositories.SettingsRepository', return_value=mock_settings_repo):
            _get_config_persistence.cache_clear()

            instance1 = _get_config_persistence()
            instance2 = _get_config_persistence()
            assert instance1 is instance2

        _get_config_persistence.cache_clear()

    def test_flask_app_reexports_work(self):
        """flask_app.config re-exports should reference the same functions."""
        from core.llm.settings import get_default_model as canonical
        from flask_app.config import get_default_model as reexported
        assert canonical is reexported


class TestImageConfigPersistenceCaching:
    """Verify image config getters in core.llm.settings share the same cached SettingsRepository instance."""

    def test_image_config_getters_construct_settings_repo_once(self):
        """Calling image config getters multiple times should only construct SettingsRepository once."""
        from core.llm.settings import _get_config_persistence
        _get_config_persistence.cache_clear()

        mock_settings_repo = MagicMock()
        mock_settings_repo.get_setting.return_value = ''

        with patch('poker.repositories.SchemaManager'), \
             patch('poker.repositories.SettingsRepository', return_value=mock_settings_repo) as mock_cls:
            _get_config_persistence.cache_clear()

            from core.llm.settings import get_image_provider, get_image_model

            get_image_provider()
            get_image_model()
            get_image_provider()  # call again to prove caching
            get_image_model()

            # SettingsRepository should be constructed exactly once
            assert mock_cls.call_count == 1

        _get_config_persistence.cache_clear()

    def test_image_config_getters_share_same_instance_with_other_getters(self):
        """Image config getters should use the same SettingsRepository as other config getters."""
        from core.llm.settings import _get_config_persistence
        _get_config_persistence.cache_clear()

        mock_settings_repo = MagicMock()
        mock_settings_repo.get_setting.return_value = ''

        with patch('poker.repositories.SchemaManager'), \
             patch('poker.repositories.SettingsRepository', return_value=mock_settings_repo) as mock_cls:
            _get_config_persistence.cache_clear()

            from core.llm.settings import get_default_model, get_image_provider

            get_default_model()
            get_image_provider()

            # Still only one SettingsRepository instance
            assert mock_cls.call_count == 1

        _get_config_persistence.cache_clear()

    def test_flask_app_image_reexports_work(self):
        """flask_app.config re-exports of image getters should reference the same functions."""
        from core.llm.settings import get_image_provider as canonical
        from flask_app.config import get_image_provider as reexported
        assert canonical is reexported
