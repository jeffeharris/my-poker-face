"""PRH-32: api_usage retention purge + the scheduled sweep's safe no-op."""

import datetime as dt
import sqlite3

import pytest

pytestmark = pytest.mark.llm


def _insert(db_path, created_at):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO api_usage (created_at, call_type, provider, model, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (created_at, "unknown", "openai", "gpt", "ok"),
        )


def test_prune_old_usage_deletes_old_keeps_recent(usage_tracker):
    db = usage_tracker.db_path
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=200)).isoformat()
    recent = dt.datetime.now(dt.timezone.utc).isoformat()
    _insert(db, old)
    _insert(db, recent)

    assert usage_tracker.prune_old_usage(90) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0] == 1


def test_prune_zero_is_noop(usage_tracker):
    db = usage_tracker.db_path
    _insert(db, (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=999)).isoformat())
    assert usage_tracker.prune_old_usage(0) == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0] == 1


def test_run_retention_sweep_noop_without_env(monkeypatch):
    """The sweep is inert (and never raises) when no retention window is set —
    the default posture in dev/tests."""
    monkeypatch.delenv("API_USAGE_RETENTION_DAYS", raising=False)
    from flask_app.services.retention_service import run_retention_sweep

    assert run_retention_sweep() == {"captures": 0, "api_usage": 0}
