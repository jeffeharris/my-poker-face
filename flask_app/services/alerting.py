"""Webhook alerting for production safety signals (PRH-28).

A logging handler that forwards high-signal log records to a Slack-compatible
webhook (``ALERT_WEBHOOK_URL``) so the existing safety signals — unhandled
ERRORs, ``[LEDGER] DRIFT RISK``, and ``[LLM BUDGET]`` (cap tripped / disabled)
— actually reach a human instead of sitting unread in stdout.

Design:
- **Opt-in / no-op by default.** With no ``ALERT_WEBHOOK_URL`` the handler is
  never attached; logging is unchanged. Dev / test / sims are unaffected.
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
from typing import Optional

# WARNING-level signals worth paging on even though they aren't ERRORs.
_PREFIXES = ("[LEDGER]", "[LLM BUDGET]")
_MODULE_LOGGER_NAME = __name__


class WebhookAlertHandler(logging.Handler):
    """Forwards alertable log records to a Slack-compatible webhook."""

    def __init__(
        self,
        webhook_url: str,
        *,
        cooldown_seconds: float = 60.0,
        max_per_minute: int = 30,
        timeout_seconds: float = 5.0,
    ) -> None:
        # WARNING floor: ERRORs and the prefixed WARNINGs ([LEDGER]/[LLM BUDGET])
        # are the alertable set; plain INFO/DEBUG never reach emit().
        super().__init__(level=logging.WARNING)
        self._url = webhook_url
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
            signature = f"{record.name}:{record.levelno}:{record.getMessage()[:120]}"
            if not self._allow(signature):
                return
            self._dispatch(self._format_text(record))
        except Exception:
            # A logging handler must never raise back into the call site.
            pass

    def _dispatch(self, text: str) -> None:
        """Send the alert without blocking the caller. Overridable in tests."""
        threading.Thread(target=self._post, args=(text,), daemon=True).start()

    def _format_text(self, record: logging.LogRecord) -> str:
        text = f"{self._tag} *{record.levelname}* `{record.name}`\n{record.getMessage()}"
        if record.exc_info:
            try:
                tb = logging.Formatter().formatException(record.exc_info)
                text += "\n```\n" + tb[-1500:] + "\n```"
            except Exception:
                pass
        return text[:3500]

    def _post(self, text: str) -> None:
        try:
            data = json.dumps({"text": text}).encode("utf-8")
            req = urllib.request.Request(
                self._url, data=data, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=self._timeout).close()  # noqa: S310 (trusted op URL)
        except Exception as exc:
            # Print, never log — logging here would re-enter the handler.
            print(f"[alerting] webhook POST failed: {exc}", file=sys.stderr)


def init_alerting() -> Optional[WebhookAlertHandler]:
    """Attach the webhook alert handler to the root logger if configured.

    No-op (returns ``None``) when ``ALERT_WEBHOOK_URL`` is unset. Idempotent:
    re-calling (e.g. a second ``create_app()`` in tests) replaces any handler a
    previous call attached rather than stacking.
    """
    url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    root = logging.getLogger()
    for existing in [h for h in root.handlers if isinstance(h, WebhookAlertHandler)]:
        root.removeHandler(existing)
    if not url:
        return None
    handler = WebhookAlertHandler(url)
    root.addHandler(handler)
    logging.getLogger(__name__).info("[ALERTING] webhook alert handler attached")
    return handler
