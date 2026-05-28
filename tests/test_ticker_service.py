"""Tests for the realtime world ticker service.

Covers pace→params mapping, the enable flag, per-cycle pace gating, and
the per-sandbox tick: passes the pace's hand_sim_prob to the refresh,
pushes lobby_tick, baselines the event marker so it doesn't replay the
backlog, and emits new world_events. The heavy refresh + event buffer
are mocked. See `docs/plans/CASH_MODE_REALTIME_TICKER.md`.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from flask_app.services import ticker_service


@dataclass
class FakeEvent:
    created_at: str
    type: str = "join"
    personality_id: str = "napoleon"


class FakeSocketIO:
    def __init__(self):
        self.emits = []  # list of (event, data, room)

    def emit(self, event, data, to=None):
        self.emits.append((event, data, to))

    def emitted(self, event):
        return [e for e in self.emits if e[0] == event]


class FakePrefsRepo:
    def __init__(self, pace=None, raises=False):
        self._pace = pace
        self._raises = raises

    def get_world_pace(self, user_id):
        if self._raises:
            raise RuntimeError("boom")
        return self._pace


@pytest.fixture(autouse=True)
def reset_state():
    ticker_service._last_marker.clear()
    ticker_service._cycle = 0
    yield
    ticker_service._last_marker.clear()
    ticker_service._cycle = 0


# --- is_enabled --------------------------------------------------------


def test_is_enabled_defaults_true(monkeypatch):
    monkeypatch.delenv("WORLD_TICKER_ENABLED", raising=False)
    assert ticker_service.is_enabled() is True


@pytest.mark.parametrize(
    "val,expected",
    [
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("true", True),
        ("1", True),
        ("", True),
    ],
)
def test_is_enabled_env(monkeypatch, val, expected):
    monkeypatch.setenv("WORLD_TICKER_ENABLED", val)
    assert ticker_service.is_enabled() is expected


# --- _resolve_pace -----------------------------------------------------


@pytest.mark.parametrize(
    "pace,expected",
    [
        ("subtle", (0.15, 3)),
        ("lively", (0.40, 1)),
        ("bustling", (0.90, 1)),
    ],
)
def test_resolve_pace_maps(monkeypatch, pace, expected):
    from flask_app import extensions

    monkeypatch.setattr(extensions, "user_prefs_repo", FakePrefsRepo(pace=pace), raising=False)
    assert ticker_service._resolve_pace("u1") == expected


def test_resolve_pace_unknown_falls_back(monkeypatch):
    from flask_app import extensions

    monkeypatch.setattr(extensions, "user_prefs_repo", FakePrefsRepo(pace="turbo"), raising=False)
    assert ticker_service._resolve_pace("u1") == ticker_service._PACE_PARAMS["lively"]


def test_resolve_pace_repo_error_falls_back(monkeypatch):
    from flask_app import extensions

    monkeypatch.setattr(extensions, "user_prefs_repo", FakePrefsRepo(raises=True), raising=False)
    assert ticker_service._resolve_pace("u1") == ticker_service._PACE_PARAMS["lively"]


# --- _tick_sandbox -----------------------------------------------------


def _patch_tick(monkeypatch, *, pace="lively", events=None):
    """Wire up the lazy imports _tick_sandbox reaches for."""
    import cash_mode.activity as activity_mod
    import cash_mode.lobby as lobby_mod
    from flask_app import extensions

    calls = {}

    def fake_refresh(**kwargs):
        calls["refresh"] = kwargs

    monkeypatch.setattr(lobby_mod, "refresh_unseated_tables", fake_refresh)
    monkeypatch.setattr(activity_mod, "recent_events", lambda *a, **k: list(events or []))
    monkeypatch.setattr(
        activity_mod,
        "serialize_event",
        lambda e: {"type": e.type, "created_at": e.created_at, "personality_id": e.personality_id},
    )
    monkeypatch.setattr(extensions, "user_prefs_repo", FakePrefsRepo(pace=pace), raising=False)
    return calls


def test_tick_passes_pace_prob_and_emits_lobby_tick(monkeypatch):
    calls = _patch_tick(monkeypatch, pace="bustling", events=[])
    ticker_service._cycle = 1
    sio = FakeSocketIO()

    ticker_service._tick_sandbox(sio, "u1", "sbx1")

    assert calls["refresh"]["hand_sim_prob"] == 0.90
    assert calls["refresh"]["sandbox_id"] == "sbx1"
    assert calls["refresh"]["user_id"] == "u1"
    ticks = sio.emitted("lobby_tick")
    assert len(ticks) == 1
    assert ticks[0][2] == "lobby:u1"  # room
    assert sio.emitted("world_event") == []  # no events this tick


def test_tick_baselines_marker_no_backlog_replay(monkeypatch):
    # First sight of u1 with a pre-existing event already in the buffer:
    # it must NOT be re-emitted (we baseline the marker to "now").
    e0 = FakeEvent(created_at="2026-05-24T12:00:00")
    _patch_tick(monkeypatch, pace="lively", events=[e0])
    ticker_service._cycle = 1
    sio = FakeSocketIO()

    ticker_service._tick_sandbox(sio, "u1", "sbx1")

    assert sio.emitted("world_event") == []
    assert ticker_service._last_marker["u1"] == "2026-05-24T12:00:00"


def test_tick_emits_new_world_events(monkeypatch):
    e_new = FakeEvent(created_at="2026-05-24T12:05:00", type="big_win")
    _patch_tick(monkeypatch, pace="lively", events=[e_new])
    # Established marker older than the new event.
    ticker_service._last_marker["u1"] = "2026-05-24T12:00:00"
    ticker_service._cycle = 1
    sio = FakeSocketIO()

    ticker_service._tick_sandbox(sio, "u1", "sbx1")

    world_events = sio.emitted("world_event")
    assert len(world_events) == 1
    assert world_events[0][1]["type"] == "big_win"
    assert world_events[0][2] == "lobby:u1"
    # Marker advances to the newest emitted event.
    assert ticker_service._last_marker["u1"] == "2026-05-24T12:05:00"


def test_tick_forwards_live_seated_pids(monkeypatch):
    # A live cash game in this sandbox must be reported as occupied to the
    # refresh, so the world ticker can't seat/bust the human's live opponent
    # elsewhere (the double-booked-persona corruption).
    from flask_app.services import game_state_service

    calls = _patch_tick(monkeypatch, pace="lively", events=[])
    monkeypatch.setattr(
        game_state_service,
        "games",
        {"cash-abc": {"sandbox_id": "sbx1", "cash_personality_ids": {"Zeus": "zeus"}}},
    )
    ticker_service._cycle = 1
    ticker_service._tick_sandbox(FakeSocketIO(), "u1", "sbx1")

    assert calls["refresh"]["live_seated_pids"] == {"zeus"}


def test_subtle_pace_skips_off_cycles(monkeypatch):
    calls = _patch_tick(monkeypatch, pace="subtle", events=[])
    sio = FakeSocketIO()

    # subtle runs every 3rd cycle; cycle 1 is skipped → no refresh/emit.
    ticker_service._cycle = 1
    ticker_service._tick_sandbox(sio, "u1", "sbx1")
    assert "refresh" not in calls
    assert sio.emits == []

    # cycle 3 runs.
    ticker_service._cycle = 3
    ticker_service._tick_sandbox(sio, "u1", "sbx1")
    assert "refresh" in calls
    assert sio.emitted("lobby_tick")
