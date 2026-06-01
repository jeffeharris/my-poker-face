"""Structured logging + per-request correlation IDs (PRH-35).

Every log record carries a ``request_id`` (set via a ``LogRecordFactory`` so it
reaches *all* handlers — the stdout handler and the PRH-28 alert webhook alike),
and each HTTP request gets a correlation id that is echoed back in the
``X-Request-ID`` response header. Set ``LOG_FORMAT=json`` for machine-parseable
output suitable for shipping to an aggregator; otherwise a human-readable line
that still includes the id. ``LOG_LEVEL`` controls the threshold.

Greenlet-safe: ``contextvars`` are copied per greenlet under the gevent worker,
so concurrent requests don't bleed ids. Socket.IO events don't go through the
HTTP before/after-request hooks, so they log ``request_id=-`` (acceptable for
now; the HTTP surface is where correlation matters most).
"""

import json
import logging
import os
import uuid
from contextvars import ContextVar

from flask import Flask, g, request

REQUEST_ID_HEADER = "X-Request-ID"

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
_configured = False


def get_request_id() -> str:
    """Current request's correlation id ('-' outside a request)."""
    return _request_id_ctx.get()


class JsonLogFormatter(logging.Formatter):
    """Compact one-line JSON: ts, level, logger, request_id, msg (+ exc)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Install the root stdout handler + the request-id record factory.

    Idempotent. Replaces any pre-existing root handlers (e.g. an earlier
    ``basicConfig``) so records aren't double-emitted and all flow through one
    formatter. Sets a ``LogRecordFactory`` that stamps ``request_id`` on every
    record, so both the stdout handler and the alert webhook can surface it.
    """
    global _configured

    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    use_json = os.environ.get("LOG_FORMAT", "").strip().lower() == "json"

    if not _configured:
        old_factory = logging.getLogRecordFactory()

        def _record_factory(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            record.request_id = _request_id_ctx.get()
            return record

        logging.setLogRecordFactory(_record_factory)
        _configured = True

    handler = logging.StreamHandler()
    if use_json:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(request_id)s] %(name)s %(levelname)s %(message)s")
        )

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    for noisy in ("werkzeug", "socketio", "engineio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def init_request_logging(app: Flask) -> None:
    """Register the per-request correlation-id hooks on ``app``.

    Honors an inbound ``X-Request-ID`` (so an upstream proxy/client id is kept
    end-to-end) or mints a short one; exposes it on ``flask.g`` + the logging
    contextvar and echoes it on the response.
    """

    @app.before_request
    def _assign_request_id():
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        g.request_id = rid
        _request_id_ctx.set(rid)

    @app.after_request
    def _emit_request_id(response):
        rid = getattr(g, "request_id", None)
        if rid:
            response.headers[REQUEST_ID_HEADER] = rid
        return response
