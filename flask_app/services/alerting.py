"""Webhook alerting for production safety signals (PRH-28).

A logging handler that forwards high-signal log records to a Slack-compatible
webhook so the existing safety signals — unhandled ERRORs, ``[LEDGER] DRIFT
RISK``, and ``[LLM BUDGET]`` (cap tripped / disabled) — actually reach a human
instead of sitting unread in stdout.

Webhook URL resolution (admin-configurable):
- The **DB setting** ``ALERT_WEBHOOK_URL`` (set via the admin Settings API)
  takes precedence, so an operator can enable/rotate alerting at runtime with
  no redeploy.
- Else the ``ALERT_WEBHOOK_URL`` **env var** (the deploy-time default).
- Else nothing — the handler is a no-op.
Resolution is cached ~30s to keep a DB read off the per-log path; the admin
update path calls :func:`invalidate_webhook_url_cache` so a change takes effect
immediately.

Design:
- **Always attached, no-op until a URL exists.** The handler is added to the
  root logger at startup regardless, so enabling it via the admin setting needs
  no restart. With no URL it returns before doing any work.
- **Non-blocking.** Each alert POSTs from a short-lived daemon thread with a
  hard timeout, so a slow or down webhook never stalls a request or the ticker.
- **Throttled.** A per-message-signature cooldown plus a global per-minute cap
  keep an error storm from flooding the channel (and from spawning unbounded
  threads).
- **Recursion-safe.** The POST path never logs through the root logger, and
  records from this module are ignored — a webhook failure cannot feed itself.

Slack webhooks accept ``{"text": ...}``; Discord webhooks accept the same shape
at their ``/slack`` suffix, so one handler covers both.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.request
from typing import Callable, Optional

# WARNING-level signals worth paging on even though they aren't ERRORs.
_PREFIXES = ("[LEDGER]", "[LLM BUDGET]", "[CASH LIFECYCLE]")
_MODULE_LOGGER_NAME = __name__

# The admin Settings key / env var name for the webhook URL.
WEBHOOK_SETTING_KEY = "ALERT_WEBHOOK_URL"

# --- webhook URL resolution (DB setting over env, short-cached) -------------
_url_cache_lock = threading.Lock()
_url_cache_value: Optional[str] = None
_url_cache_at: float = 0.0
_URL_CACHE_TTL_SECONDS = 30.0


def _read_webhook_url_uncached() -> str:
    """DB setting (admin-configurable) over env. Empty string when neither."""
    try:
        from flask_app import extensions

        repo = getattr(extensions, "settings_repo", None)
        if repo is not None:
            value = repo.get_setting(WEBHOOK_SETTING_KEY, "")
            if value:
                return value.strip()
    except Exception:
        # Settings store unavailable (early startup / non-Flask context) — fall
        # back to env rather than letting alerting resolution raise.
        pass
    return os.environ.get(WEBHOOK_SETTING_KEY, "").strip()


def get_webhook_url() -> str:
    """Current alert webhook URL (DB setting over env), cached ~30s."""
    global _url_cache_value, _url_cache_at
    now = time.monotonic()
    with _url_cache_lock:
        if _url_cache_value is not None and (now - _url_cache_at) < _URL_CACHE_TTL_SECONDS:
            return _url_cache_value
    value = _read_webhook_url_uncached()
    with _url_cache_lock:
        _url_cache_value = value
        _url_cache_at = now
    return value


def invalidate_webhook_url_cache() -> None:
    """Force the next get_webhook_url() to re-read. Call after an admin update."""
    global _url_cache_value, _url_cache_at
    with _url_cache_lock:
        _url_cache_value = None
        _url_cache_at = 0.0


def mask_url(url: Optional[str]) -> str:
    """Mask a webhook URL for display (it's a bearer capability secret)."""
    url = (url or "").strip()
    if not url:
        return ""
    if len(url) <= 12:
        return "•" * len(url)
    return f"{url[:20]}…{url[-4:]}"


class WebhookAlertHandler(logging.Handler):
    """Forwards alertable log records to a Slack-compatible webhook."""

    def __init__(
        self,
        url_provider: Callable[[], str] = get_webhook_url,
        *,
        cooldown_seconds: float = 60.0,
        max_per_minute: int = 30,
        timeout_seconds: float = 5.0,
    ) -> None:
        # WARNING floor: ERRORs and the prefixed WARNINGs ([LEDGER]/[LLM BUDGET])
        # are the alertable set; plain INFO/DEBUG never reach emit().
        super().__init__(level=logging.WARNING)
        self._url_provider = url_provider
        self._cooldown = cooldown_seconds
        self._max_per_minute = max_per_minute
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._last_sent: dict[str, float] = {}
        self._window_start = 0.0
        self._window_count = 0
        self._tag = f"[{os.environ.get('FLASK_ENV', '?')}]"

    @staticmethod
    def _is_alertable(record: logging.LogRecord) -> bool:
        if record.name == _MODULE_LOGGER_NAME:
            return False  # recursion guard: never alert on our own failures
        if record.levelno >= logging.ERROR:
            return True
        return any(p in record.getMessage() for p in _PREFIXES)

    def _allow(self, signature: str) -> bool:
        """Per-signature cooldown + global per-minute cap (thread-safe)."""
        now = time.monotonic()
        with self._lock:
            if now - self._window_start >= 60.0:
                self._window_start = now
                self._window_count = 0
            if self._window_count >= self._max_per_minute:
                return False
            last = self._last_sent.get(signature)
            if last is not None and (now - last) < self._cooldown:
                return False
            self._last_sent[signature] = now
            self._window_count += 1
            if len(self._last_sent) > 512:  # opportunistic prune
                cutoff = now - self._cooldown
                self._last_sent = {k: v for k, v in self._last_sent.items() if v >= cutoff}
            return True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not self._is_alertable(record):
                return
            url = (self._url_provider() or "").strip()
            if not url:
                return  # no destination configured -> no-op (don't spend throttle)
            signature = f"{record.name}:{record.levelno}:{record.getMessage()[:120]}"
            if not self._allow(signature):
                return
            self._dispatch(url, self._format_text(record))
        except Exception:
            # A logging handler must never raise back into the call site.
            pass

    def _dispatch(self, url: str, text: str) -> None:
        """Send the alert without blocking the caller. Overridable in tests."""
        threading.Thread(target=self._post, args=(url, text), daemon=True).start()

    def _format_text(self, record: logging.LogRecord) -> str:
        text = f"{self._tag} *{record.levelname}* `{record.name}`\n{record.getMessage()}"
        if record.exc_info:
            try:
                tb = logging.Formatter().formatException(record.exc_info)
                text += "\n```\n" + tb[-1500:] + "\n```"
            except Exception:
                pass
        return text[:3500]

    def _post(self, url: str, text: str) -> None:
        try:
            data = json.dumps({"text": text}).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=self._timeout).close()  # noqa: S310 (op-configured URL)
        except Exception as exc:
            # Print, never log — logging here would re-enter the handler.
            print(f"[alerting] webhook POST failed: {exc}", file=sys.stderr)


def init_alerting() -> WebhookAlertHandler:
    """Attach the webhook alert handler to the root logger (idempotent).

    Always attaches the handler; it stays a no-op until a webhook URL is
    configured (env ``ALERT_WEBHOOK_URL`` or the admin DB setting), so alerting
    can be enabled at runtime with no restart. Re-calling (e.g. a second
    ``create_app()`` in tests) replaces the prior handler rather than stacking.
    """
    root = logging.getLogger()
    for existing in [h for h in root.handlers if isinstance(h, WebhookAlertHandler)]:
        root.removeHandler(existing)
    handler = WebhookAlertHandler()
    root.addHandler(handler)
    logging.getLogger(__name__).info(
        "[ALERTING] webhook alert handler attached (%s)",
        "URL configured"
        if get_webhook_url()
        else "no URL yet — set ALERT_WEBHOOK_URL env or the admin setting",
    )
    return handler
