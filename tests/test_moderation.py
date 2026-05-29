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


def test_short_timeout_is_passed_and_bounded(monkeypatch):
    """The per-call timeout must be short (not the shared 600s read timeout),
    so a stalled moderation endpoint fails open fast instead of hanging the
    request (PRH-18 class)."""
    _enable(monkeypatch)
    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        result = MagicMock()
        result.flagged = False
        result.categories.model_dump.return_value = {}
        return MagicMock(results=[result])

    client = MagicMock()
    client.moderations.create.side_effect = _create
    monkeypatch.setattr(mod, "_get_client", lambda: client)

    mod.moderate_text("hello there")
    assert captured.get("timeout") == mod._TIMEOUT_SECONDS
    assert 0 < mod._TIMEOUT_SECONDS <= 15  # bounded, not the 600s default


def test_fail_open_on_api_error(monkeypatch):
    _enable(monkeypatch)

    def boom():
        raise RuntimeError("moderation API down")

    monkeypatch.setattr(mod, "_get_client", boom)
    r = mod.moderate_text("anything")
    # Outage must not block the save.
    assert r.flagged is False and r.checked is False


# --- player chat screening (length cap + moderation) ------------------------

def test_player_chat_rejection_too_long():
    from flask_app.routes import game_routes as gr

    rejection = gr._player_chat_rejection("x" * (gr.MAX_PLAYER_CHAT_LEN + 1))
    assert rejection and rejection["code"] == "CHAT_TOO_LONG"


def test_player_chat_rejection_flagged(monkeypatch):
    from flask_app.routes import game_routes as gr

    monkeypatch.setattr(
        gr, "moderate_text", lambda t: mod.ModerationResult(flagged=True, categories=["hate"])
    )
    rejection = gr._player_chat_rejection("something nasty")
    assert rejection and rejection["code"] == "MODERATION_REJECTED"


def test_player_chat_rejection_clean(monkeypatch):
    from flask_app.routes import game_routes as gr

    monkeypatch.setattr(gr, "moderate_text", lambda t: mod.ModerationResult(flagged=False))
    assert gr._player_chat_rejection("nice hand, well played") is None
