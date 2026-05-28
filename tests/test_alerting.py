"""Tests for the webhook alert handler (PRH-28).

Covers alertable-record gating, the throttle (per-signature cooldown + global
per-minute cap), the no-op-when-unconfigured behavior, the DB-over-env URL
resolution (admin-configurable), and masking — without any network I/O
(``_dispatch`` is stubbed to record texts; the URL is injected via a provider).
"""

import logging

import pytest

from flask_app.services.alerting import (
    WebhookAlertHandler,
    get_webhook_url,
    init_alerting,
    invalidate_webhook_url_cache,
    mask_url,
)


def _record(level, msg, name="some.logger", exc_info=None):
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=exc_info
    )


@pytest.fixture
def handler():
    """Handler with a fixed URL provider and a no-network dispatch sink."""
    h = WebhookAlertHandler(
        url_provider=lambda: "http://example.invalid/hook",
        cooldown_seconds=60.0,
        max_per_minute=30,
    )
    h.sent = []
    h._dispatch = lambda url, text: h.sent.append(text)  # no network
    return h


# --- alertable gating -------------------------------------------------------

def test_error_is_alertable(handler):
    assert handler._is_alertable(_record(logging.ERROR, "boom")) is True


def test_warning_without_prefix_is_not_alertable(handler):
    assert handler._is_alertable(_record(logging.WARNING, "just a warning")) is False


@pytest.mark.parametrize("msg", ["[LEDGER] DRIFT RISK: ...", "[LLM BUDGET] blocked decision call"])
def test_prefixed_warning_is_alertable(handler, msg):
    assert handler._is_alertable(_record(logging.WARNING, msg)) is True


def test_own_module_records_are_ignored(handler):
    # Recursion guard: our own failures must never alert.
    rec = _record(logging.ERROR, "webhook POST failed", name="flask_app.services.alerting")
    assert handler._is_alertable(rec) is False


def test_emit_dispatches_alertable_and_skips_rest(handler):
    handler.emit(_record(logging.ERROR, "kaboom"))
    handler.emit(_record(logging.INFO, "noise"))  # below WARNING floor anyway
    handler.emit(_record(logging.WARNING, "plain warning"))  # not alertable
    assert len(handler.sent) == 1
    assert "kaboom" in handler.sent[0]


def test_no_url_is_noop():
    """An alertable record with no configured URL dispatches nothing."""
    h = WebhookAlertHandler(url_provider=lambda: "")
    h.sent = []
    h._dispatch = lambda url, text: h.sent.append(text)
    h.emit(_record(logging.ERROR, "would-be alert"))
    assert h.sent == []


# --- throttle ---------------------------------------------------------------

def test_same_signature_is_throttled(handler):
    handler.emit(_record(logging.ERROR, "repeat me"))
    handler.emit(_record(logging.ERROR, "repeat me"))
    assert len(handler.sent) == 1  # second within cooldown is suppressed


def test_distinct_signatures_pass(handler):
    handler.emit(_record(logging.ERROR, "first distinct"))
    handler.emit(_record(logging.ERROR, "second distinct"))
    assert len(handler.sent) == 2


def test_global_per_minute_cap(handler):
    handler._max_per_minute = 3
    for i in range(10):
        handler.emit(_record(logging.ERROR, f"unique error {i}"))
    assert len(handler.sent) == 3  # capped within the window


# --- URL resolution (DB setting over env) -----------------------------------

class _FakeSettingsRepo:
    def __init__(self, value):
        self._value = value

    def get_setting(self, key, default=None):
        return self._value if self._value is not None else default


def test_env_url_used_when_no_db_setting(monkeypatch):
    from flask_app import extensions

    monkeypatch.setattr(extensions, "settings_repo", None, raising=False)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://env.example/hook")
    invalidate_webhook_url_cache()
    assert get_webhook_url() == "http://env.example/hook"


def test_db_setting_overrides_env(monkeypatch):
    from flask_app import extensions

    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://env.example/hook")
    monkeypatch.setattr(extensions, "settings_repo", _FakeSettingsRepo("http://db.example/hook"))
    invalidate_webhook_url_cache()
    assert get_webhook_url() == "http://db.example/hook"


def test_invalidate_forces_refresh(monkeypatch):
    from flask_app import extensions

    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(extensions, "settings_repo", _FakeSettingsRepo("http://one.example"))
    invalidate_webhook_url_cache()
    assert get_webhook_url() == "http://one.example"
    monkeypatch.setattr(extensions, "settings_repo", _FakeSettingsRepo("http://two.example"))
    invalidate_webhook_url_cache()
    assert get_webhook_url() == "http://two.example"


def test_mask_url():
    assert mask_url("") == ""
    assert mask_url(None) == ""
    masked = mask_url("https://hooks.slack.com/services/T0/B0/secrettoken123")
    assert "secrettoken" not in masked and "…" in masked
    assert mask_url("short") == "•••••"  # short values fully masked


# --- init -------------------------------------------------------------------

def test_init_always_attaches_and_is_idempotent(monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    root = logging.getLogger()
    try:
        h1 = init_alerting()  # attaches even with no URL (no-op until configured)
        assert isinstance(h1, WebhookAlertHandler)
        init_alerting()  # second call replaces rather than stacks
        attached = [h for h in root.handlers if isinstance(h, WebhookAlertHandler)]
        assert len(attached) == 1
    finally:
        for h in [h for h in root.handlers if isinstance(h, WebhookAlertHandler)]:
            root.removeHandler(h)
