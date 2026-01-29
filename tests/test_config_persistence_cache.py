"""Tests for T2-05: Cached GamePersistence in config getters."""

from unittest.mock import patch, MagicMock


class TestConfigPersistenceCaching:
    """Verify flask_app/config.py config getters share a single GamePersistence instance."""

    def test_config_getters_construct_persistence_once(self):
        """Calling multiple config getters should only construct GamePersistence once."""
        from flask_app.config import _get_config_persistence
        _get_config_persistence.cache_clear()

        mock_persistence = MagicMock()
        mock_persistence.get_setting.return_value = ''

        with patch('poker.persistence.GamePersistence', return_value=mock_persistence) as mock_cls:
            _get_config_persistence.cache_clear()

            from flask_app.config import (
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

            # GamePersistence should be constructed exactly once
            assert mock_cls.call_count == 1

        _get_config_persistence.cache_clear()

    def test_config_getters_share_same_instance(self):
        """All config getters should use the exact same persistence object."""
        from flask_app.config import _get_config_persistence
        _get_config_persistence.cache_clear()

        mock_persistence = MagicMock()
        mock_persistence.get_setting.return_value = ''

        with patch('poker.persistence.GamePersistence', return_value=mock_persistence):
            _get_config_persistence.cache_clear()

            instance1 = _get_config_persistence()
            instance2 = _get_config_persistence()
            assert instance1 is instance2

        _get_config_persistence.cache_clear()


class TestImageConfigPersistenceCaching:
    """Verify image config getters in flask_app/config.py share the same cached GamePersistence instance."""

    def test_image_config_getters_construct_persistence_once(self):
        """Calling image config getters multiple times should only construct GamePersistence once."""
        from flask_app.config import _get_config_persistence
        _get_config_persistence.cache_clear()

        mock_persistence = MagicMock()
        mock_persistence.get_setting.return_value = ''

        with patch('poker.persistence.GamePersistence', return_value=mock_persistence) as mock_cls:
            _get_config_persistence.cache_clear()

            from flask_app.config import get_image_provider, get_image_model

            get_image_provider()
            get_image_model()
            get_image_provider()  # call again to prove caching
            get_image_model()

            # GamePersistence should be constructed exactly once
            assert mock_cls.call_count == 1

        _get_config_persistence.cache_clear()

    def test_image_config_getters_share_same_instance_with_other_getters(self):
        """Image config getters should use the same persistence as other config getters."""
        from flask_app.config import _get_config_persistence
        _get_config_persistence.cache_clear()

        mock_persistence = MagicMock()
        mock_persistence.get_setting.return_value = ''

        with patch('poker.persistence.GamePersistence', return_value=mock_persistence) as mock_cls:
            _get_config_persistence.cache_clear()

            from flask_app.config import get_default_model, get_image_provider

            get_default_model()
            get_image_provider()

            # Still only one GamePersistence instance
            assert mock_cls.call_count == 1

        _get_config_persistence.cache_clear()
