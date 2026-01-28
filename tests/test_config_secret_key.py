"""Tests for SECRET_KEY configuration behavior (T1-19)."""

import importlib
import os
from unittest import mock

import pytest


def _reload_config(env_overrides: dict):
    """Reload flask_app.config with the given environment variables.

    Mocks load_dotenv to prevent .env file from overriding test environment.
    """
    with mock.patch.dict(os.environ, env_overrides, clear=False):
        # Remove keys not in overrides to simulate absence
        for key in ('SECRET_KEY', 'FLASK_ENV', 'FLASK_DEBUG'):
            if key not in env_overrides:
                os.environ.pop(key, None)

        # Prevent load_dotenv from loading .env file during reload
        with mock.patch('dotenv.load_dotenv'):
            import flask_app.config as config_module
            importlib.reload(config_module)
            return config_module


class TestSecretKeyConfig:

    def test_dev_mode_uses_stable_default_when_no_env_var(self):
        """In development mode without SECRET_KEY env var, a stable default is used."""
        config = _reload_config({'FLASK_ENV': 'development'})
        assert config.SECRET_KEY == 'dev-secret-key-not-for-production'

    def test_dev_mode_stable_across_reloads(self):
        """Dev default SECRET_KEY is the same across reloads (not random)."""
        config1 = _reload_config({'FLASK_ENV': 'development'})
        key1 = config1.SECRET_KEY
        config2 = _reload_config({'FLASK_ENV': 'development'})
        key2 = config2.SECRET_KEY
        assert key1 == key2

    def test_dev_mode_respects_env_var(self):
        """In dev mode, an explicit SECRET_KEY env var is used."""
        config = _reload_config({'FLASK_ENV': 'development', 'SECRET_KEY': 'my-custom-key'})
        assert config.SECRET_KEY == 'my-custom-key'

    def test_debug_mode_treated_as_development(self):
        """FLASK_DEBUG=1 is treated as development mode."""
        config = _reload_config({'FLASK_DEBUG': '1'})
        assert config.SECRET_KEY == 'dev-secret-key-not-for-production'

    def test_prod_mode_raises_without_secret_key(self):
        """Production mode raises RuntimeError if SECRET_KEY is not set."""
        with pytest.raises(RuntimeError, match="SECRET_KEY environment variable is required"):
            _reload_config({'FLASK_ENV': 'production'})

    def test_prod_mode_error_includes_generation_command(self):
        """The RuntimeError message includes a command to generate a key."""
        with pytest.raises(RuntimeError, match="secrets.token_hex"):
            _reload_config({'FLASK_ENV': 'production'})

    def test_prod_mode_uses_env_var(self):
        """Production mode uses the SECRET_KEY from environment."""
        config = _reload_config({'FLASK_ENV': 'production', 'SECRET_KEY': 'prod-secret-123'})
        assert config.SECRET_KEY == 'prod-secret-123'
