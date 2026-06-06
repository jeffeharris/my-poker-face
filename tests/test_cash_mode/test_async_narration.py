"""Tests for async ticker narration (ASYNC_TICKER_NARRATION.md Step 2).

The off-grid economics commit in-tick with a templated placeholder (the
duration is chosen system-side); the LLM flavor line is produced off the
tick and recorded into the activity feed when it returns. These tests
exercise:

  - the narrow `update_narration` repo methods (so the active-vice /
    whereabouts surfaces show the real line, not the placeholder), and
  - `ticker_service._narrate_and_emit` running synchronously: it narrates,
    updates the state row, and records a feed event with the real line —
    which the next ticker poll would emit.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.ai_side_hustle import HustleStartResult
from cash_mode.ai_vice_spending import ViceStartResult
from poker.repositories.side_hustle_state_repository import SideHustleState
from poker.repositories.vice_state_repository import ViceState

SBX = "test-sandbox-async"
NOW = datetime(2026, 6, 7, 12, 0, 0)


# --- update_narration repo methods ------------------------------------------


def test_vice_update_narration(repos):
    repo = repos["vice_state_repo"]
    repo.insert_vice_state(
        ViceState(
            personality_id="napoleon",
            sandbox_id=SBX,
            started_at=NOW,
            ends_at=NOW + timedelta(hours=2),
            amount=2500,
            duration_bucket="long",
            narration="napoleon stepped out to spend $2,500 on something",
        )
    )
    changed = repo.update_narration(
        "napoleon", sandbox_id=SBX, narration="Napoleon commissioned a bronze bust"
    )
    assert changed is True
    assert repo.load("napoleon", sandbox_id=SBX).narration == (
        "Napoleon commissioned a bronze bust"
    )


def test_vice_update_narration_missing_row(repos):
    repo = repos["vice_state_repo"]
    assert repo.update_narration("ghost", sandbox_id=SBX, narration="x") is False


def test_hustle_update_narration(repos):
    repo = repos["side_hustle_state_repo"]
    repo.insert_side_hustle_state(
        SideHustleState(
            personality_id="hemingway",
            sandbox_id=SBX,
            started_at=NOW,
            ends_at=NOW + timedelta(hours=1),
            amount=500,
            duration_bucket="short",
            narration="hemingway stepped out to earn $500 on the side",
        )
    )
    changed = repo.update_narration(
        "hemingway", sandbox_id=SBX, narration="Hemingway ghost-wrote a column"
    )
    assert changed is True
    assert repo.load("hemingway", sandbox_id=SBX).narration == ("Hemingway ghost-wrote a column")


def test_hustle_update_narration_missing_row(repos):
    repo = repos["side_hustle_state_repo"]
    assert repo.update_narration("ghost", sandbox_id=SBX, narration="x") is False


# --- _narrate_and_emit (the off-tick greenlet body, run synchronously) ------


def _clear_feed():
    from cash_mode import activity

    with activity._events_lock:
        activity._events.clear()


def test_narrate_and_emit_vice_records_real_line_and_updates_row(repos, monkeypatch):
    from cash_mode import activity
    from flask_app import extensions
    from flask_app.services import ticker_service

    _clear_feed()
    monkeypatch.setattr(extensions, "personality_repo", repos["personality_repo"], raising=False)
    monkeypatch.setattr(extensions, "vice_state_repo", repos["vice_state_repo"], raising=False)

    # The in-tick placeholder row already exists when the greenlet runs.
    repos["vice_state_repo"].insert_vice_state(
        ViceState(
            personality_id="napoleon",
            sandbox_id=SBX,
            started_at=NOW,
            ends_at=NOW + timedelta(hours=2),
            amount=2500,
            duration_bucket="long",
            narration="napoleon stepped out to spend $2,500 on something",
        )
    )

    psych = {"confidence": 0.7, "composure": 0.4, "energy": 0.6}
    start = ViceStartResult(
        personality_id="napoleon",
        amount=2500,
        duration_bucket="long",
        started_at=NOW,
        ends_at=NOW + timedelta(hours=2),
        narration="napoleon stepped out to spend $2,500 on something",  # placeholder
        excess_ratio=3.8,
        pressure=0.4,
        psychology_snapshot=psych,
    )

    with patch(
        "cash_mode.vice_narration.narrate_vice",
        return_value="Napoleon commissioned an oversized bronze bust",
    ) as mock_narrate:
        ticker_service._narrate_and_emit("vice", [start], SBX)

    # The flavor LLM was given the carried psych snapshot + the chosen bucket.
    assert mock_narrate.call_args.args[2] == psych
    assert mock_narrate.call_args.args[3] == "long"

    # Feed event carries the REAL line, not the placeholder.
    events = activity.recent_events(limit=10, sandbox_id=SBX)
    vice_events = [e for e in events if e.type == "vice_start"]
    assert len(vice_events) == 1
    assert "bronze bust" in vice_events[0].message

    # State row was updated so whereabouts/active-vice show the real line too.
    assert (
        repos["vice_state_repo"].load("napoleon", sandbox_id=SBX).narration
        == "Napoleon commissioned an oversized bronze bust"
    )


def test_narrate_and_emit_hustle_records_real_line_and_updates_row(repos, monkeypatch):
    from cash_mode import activity
    from flask_app import extensions
    from flask_app.services import ticker_service

    _clear_feed()
    monkeypatch.setattr(extensions, "personality_repo", repos["personality_repo"], raising=False)
    monkeypatch.setattr(
        extensions, "side_hustle_state_repo", repos["side_hustle_state_repo"], raising=False
    )

    repos["side_hustle_state_repo"].insert_side_hustle_state(
        SideHustleState(
            personality_id="hemingway",
            sandbox_id=SBX,
            started_at=NOW,
            ends_at=NOW + timedelta(hours=1),
            amount=500,
            duration_bucket="short",
            narration="hemingway stepped out to earn $500 on the side",
        )
    )

    start = HustleStartResult(
        personality_id="hemingway",
        amount=500,
        duration_bucket="short",
        started_at=NOW,
        ends_at=NOW + timedelta(hours=1),
        narration="hemingway stepped out to earn $500 on the side",
        deficit_ratio=0.9,
    )

    with patch(
        "cash_mode.side_hustle_narration.narrate_side_hustle",
        return_value="Hemingway ghost-wrote a column overnight",
    ) as mock_narrate:
        ticker_service._narrate_and_emit("hustle", [start], SBX)

    assert mock_narrate.call_args.args[2] == "short"

    events = activity.recent_events(limit=10, sandbox_id=SBX)
    hustle_events = [e for e in events if e.type == "hustle_start"]
    assert len(hustle_events) == 1
    assert "ghost-wrote" in hustle_events[0].message
    assert (
        repos["side_hustle_state_repo"].load("hemingway", sandbox_id=SBX).narration
        == "Hemingway ghost-wrote a column overnight"
    )


def test_narrate_and_emit_failsoft_never_raises(repos, monkeypatch):
    """A narration error in the greenlet must not propagate (it would crash
    the ticker's background task)."""
    from cash_mode import activity
    from flask_app import extensions
    from flask_app.services import ticker_service

    _clear_feed()
    monkeypatch.setattr(extensions, "personality_repo", repos["personality_repo"], raising=False)
    monkeypatch.setattr(extensions, "vice_state_repo", repos["vice_state_repo"], raising=False)

    start = ViceStartResult(
        personality_id="napoleon",
        amount=2500,
        duration_bucket="long",
        started_at=NOW,
        ends_at=NOW + timedelta(hours=2),
        narration="placeholder",
        excess_ratio=3.8,
        pressure=0.4,
    )

    with patch(
        "cash_mode.vice_narration.narrate_vice",
        side_effect=RuntimeError("LLM exploded"),
    ):
        # Must not raise.
        ticker_service._narrate_and_emit("vice", [start], SBX)

    # No event recorded for the failed narration.
    events = activity.recent_events(limit=10, sandbox_id=SBX)
    assert [e for e in events if e.type == "vice_start"] == []


def test_narrate_and_emit_empty_is_noop():
    from flask_app.services import ticker_service

    # No starts → returns immediately, no error.
    ticker_service._narrate_and_emit("vice", [], SBX)
