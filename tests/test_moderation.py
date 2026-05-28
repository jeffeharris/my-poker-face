"""Tests for core.moderation (PRH-27 text moderation).

Mocks the OpenAI moderation client — no network. Covers the gating (disabled /
no key / empty), a flagged hit, a clean pass, and fail-open on API error.
"""

from unittest.mock import MagicMock

import core.moderation as mod


def _fake_client(flagged: bool, categories: dict):
    result = MagicMock()
    result.flagged = flagged
    result.categories.model_dump.return_value = categories
    client = MagicMock()
    client.moderations.create.return_value = MagicMock(results=[result])
    return client


def _enable(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MODERATION_ENABLED", "true")


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert mod.is_enabled() is False
    r = mod.moderate_text("anything at all")
    assert r.flagged is False and r.checked is False


def test_env_optout(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MODERATION_ENABLED", "false")
    assert mod.is_enabled() is False
    r = mod.moderate_text("anything")
    assert r.checked is False


def test_empty_text_is_noop(monkeypatch):
    _enable(monkeypatch)
    assert mod.moderate_text("   ").checked is False


def test_flagged_content(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(mod, "_get_client", lambda: _fake_client(True, {"hate": True, "violence": False}))
    r = mod.moderate_text("something nasty")
    assert r.flagged is True
    assert r.checked is True
    assert "hate" in r.categories and "violence" not in r.categories


def test_clean_content(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(mod, "_get_client", lambda: _fake_client(False, {"hate": False}))
    r = mod.moderate_text("I love poker and good sportsmanship")
    assert r.flagged is False and r.checked is True


def test_fail_open_on_api_error(monkeypatch):
    _enable(monkeypatch)

    def boom():
        raise RuntimeError("moderation API down")

    monkeypatch.setattr(mod, "_get_client", boom)
    r = mod.moderate_text("anything")
    # Outage must not block the save.
    assert r.flagged is False and r.checked is False
