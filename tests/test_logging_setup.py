"""PRH-35: structured logging + per-request correlation ids."""

import json
import logging

import pytest
from flask import Flask

from flask_app.logging_setup import (
    REQUEST_ID_HEADER,
    JsonLogFormatter,
    configure_logging,
    init_request_logging,
)

pytestmark = pytest.mark.flask


def test_record_factory_stamps_request_id():
    configure_logging()  # idempotent
    record = logging.getLogger("prh35").makeRecord("prh35", logging.INFO, "f", 1, "hi", None, None)
    assert hasattr(record, "request_id")  # every record carries it (default '-')


def test_json_formatter_emits_structured_fields():
    record = logging.LogRecord("prh35", logging.WARNING, "f", 1, "hello %s", ("world",), None)
    record.request_id = "abc123"
    out = json.loads(JsonLogFormatter().format(record))
    assert out["request_id"] == "abc123"
    assert out["level"] == "WARNING"
    assert out["logger"] == "prh35"
    assert out["msg"] == "hello world"


def _app_with_request_logging():
    app = Flask(__name__)
    init_request_logging(app)

    @app.route("/ping")
    def ping():
        return "ok"

    return app


def test_response_carries_a_minted_request_id():
    with _app_with_request_logging().test_client() as client:
        resp = client.get("/ping")
        assert resp.headers.get(REQUEST_ID_HEADER)  # minted when none supplied


def test_inbound_request_id_is_preserved():
    with _app_with_request_logging().test_client() as client:
        resp = client.get("/ping", headers={REQUEST_ID_HEADER: "client-xyz"})
        assert resp.headers.get(REQUEST_ID_HEADER) == "client-xyz"
