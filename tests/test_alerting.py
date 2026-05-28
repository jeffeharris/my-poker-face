"""Tests for the webhook alert handler (PRH-28).

Covers the alertable-record gating, the throttle (per-signature cooldown +
global per-minute cap), and the opt-in/no-op init — without any network I/O
(``_dispatch`` is stubbed to record texts).
"""

import logging

import pytest

from flask_app.services.alerting import WebhookAlertHandler, init_alerting


def _record(level, msg, name="some.logger", exc_info=None):
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=exc_info
    )


@pytest.fixture
def handler():
    h = WebhookAlertHandler("http://example.invalid/hook", cooldown_seconds=60.0, max_per_minute=30)
    h.sent = []
    h._dispatch = lambda text: h.sent.append(text)  # no network
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


# --- init -------------------------------------------------------------------

def test_init_is_noop_without_url(monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    root = logging.getLogger()
    before = [h for h in root.handlers if isinstance(h, WebhookAlertHandler)]
    assert init_alerting() is None
    after = [h for h in root.handlers if isinstance(h, WebhookAlertHandler)]
    assert len(after) == len(before)


def test_init_attaches_and_is_idempotent(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://example.invalid/hook")
    root = logging.getLogger()
    try:
        h1 = init_alerting()
        assert isinstance(h1, WebhookAlertHandler)
        # Second call replaces rather than stacks.
        init_alerting()
        attached = [h for h in root.handlers if isinstance(h, WebhookAlertHandler)]
        assert len(attached) == 1
    finally:
        for h in [h for h in root.handlers if isinstance(h, WebhookAlertHandler)]:
            root.removeHandler(h)
