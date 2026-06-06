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


# --- Career M2: emergent vouch firing on the tick ----------------------------


def _vouch_stubs(
    monkeypatch, *, career_active=True, tutorial_complete=True, inbound=None, seated=None
):
    """Wire stub repos onto extensions for the vouch-evaluation tests.

    `inbound`: {ai_id: (respect, likability)}; `seated`: {ai_id: table_id} (which
    cardroom each AI sits at). Returns the dict of saved CareerProgress by key."""
    from cash_mode.tables import CashTableState, ai_slot
    from flask_app import extensions
    from poker.memory.opponent_model import RelationshipState
    from poker.repositories.career_progress_repository import CareerProgress

    saved = {}

    class Repo:
        def load(self, sb, owner):
            return saved.get((sb, owner)) or CareerProgress(
                sandbox_id=sb,
                owner_id=owner,
                career_active=career_active,
                tutorial_complete=tutorial_complete,
            )

        def save(self, prog, now=None):
            saved[(prog.sandbox_id, prog.owner_id)] = prog

    class RelRepo:
        def load_inbound_relationships(self, opp, now=None):
            return {
                ai: RelationshipState(respect=r, likability=lk)
                for ai, (r, lk) in (inbound or {}).items()
            }

        def resolve_home_table(self, ai_id, *, sandbox_id, eligible_table_ids, min_hands=50):
            # Stub: an AI's home table is where it's seated (the home-table
            # *counter* itself is covered by tests/test_cash_mode/test_ai_home_table.py;
            # here we only exercise _maybe_fire_vouches' ranking/gating). Returns
            # None when between rooms / not an eligible lobby table.
            tid = (seated or {}).get(ai_id)
            return tid if tid in eligible_table_ids else None

    class TableRepo:
        def list_all_tables(self, sandbox_id=None):
            tables = {}
            for ai, tid in (seated or {}).items():
                t = tables.get(tid) or CashTableState(
                    table_id=tid, stake_label="$2", table_type="lobby", name=tid
                )
                t.seats = (t.seats or []) + [ai_slot(ai, 100)]
                tables[tid] = t
            return list(tables.values())

    monkeypatch.setattr(extensions, "career_progress_repo", Repo(), raising=False)
    monkeypatch.setattr(extensions, "relationship_repo", RelRepo(), raising=False)
    monkeypatch.setattr(extensions, "cash_table_repo", TableRepo(), raising=False)
    return saved


def test_vouch_fires_for_warmest_ready_ai(monkeypatch):
    from cash_mode import economy_flags

    monkeypatch.setattr(economy_flags, "CAREER_VOUCH_ENABLED", True)
    saved = _vouch_stubs(
        monkeypatch,
        inbound={
            "cleopatra": (0.9, 0.95),  # ready, warmest
            "shakespeare": (0.9, 0.75),  # ready, cooler
            "loose_larry": (0.0, 0.0),  # played but cold → not ready
        },
        seated={"cleopatra": "cash-table-2-007", "shakespeare": "cash-table-2-003"},
    )
    ticker_service._maybe_fire_vouches("owner1", "sb1")
    prog = saved[("sb1", "owner1")]
    assert "cleopatra" in prog.vouched_by  # warmest fired
    assert "cash-table-2-007" in prog.revealed_table_ids  # its room revealed
    assert "shakespeare" not in prog.vouched_by  # only one vouch per tick


def test_vouch_no_op_when_flag_off(monkeypatch):
    from cash_mode import economy_flags

    monkeypatch.setattr(economy_flags, "CAREER_VOUCH_ENABLED", False)
    saved = _vouch_stubs(
        monkeypatch,
        inbound={"cleopatra": (0.9, 0.95)},
        seated={"cleopatra": "cash-table-2-007"},
    )
    ticker_service._maybe_fire_vouches("owner1", "sb1")
    assert saved == {}  # nothing evaluated, nothing saved


def test_vouch_no_op_before_graduation(monkeypatch):
    from cash_mode import economy_flags

    monkeypatch.setattr(economy_flags, "CAREER_VOUCH_ENABLED", True)
    saved = _vouch_stubs(
        monkeypatch,
        tutorial_complete=False,
        inbound={"cleopatra": (0.9, 0.95)},
        seated={"cleopatra": "cash-table-2-007"},
    )
    ticker_service._maybe_fire_vouches("owner1", "sb1")
    assert saved == {}  # mid-tutorial → no emergent vouches


def test_vouch_skips_unseated_voucher(monkeypatch):
    from cash_mode import economy_flags

    monkeypatch.setattr(economy_flags, "CAREER_VOUCH_ENABLED", True)
    saved = _vouch_stubs(
        monkeypatch,
        inbound={"cleopatra": (0.9, 0.95)},
        seated={},  # not at any table
    )
    ticker_service._maybe_fire_vouches("owner1", "sb1")
    # Ready but between rooms → no reveal this tick (retry next tick).
    assert saved == {} or "cleopatra" not in saved[("sb1", "owner1")].vouched_by


# --- tournament world-tick hook (P3.7) ---------------------------------


def test_tournament_hook_inert_when_flag_off(monkeypatch):
    # Default flag is OFF: the hook must not touch the tournament services.
    from flask_app.services import tournament_ticker

    monkeypatch.setattr(
        tournament_ticker,
        "advance_owner_tournament",
        lambda **k: (_ for _ in ()).throw(AssertionError("must not run when flag off")),
    )
    # A clean no-op — no exception bubbles out.
    ticker_service._maybe_tick_tournament("u1", "sbx1")


def test_tournament_hook_records_events_when_enabled(monkeypatch):
    import cash_mode.activity as activity_mod
    import cash_mode.economy_flags as flags
    from flask_app import extensions
    from flask_app.services import tournament_invites, tournament_ticker

    monkeypatch.setattr(flags, "TOURNAMENT_CIRCUIT_ENABLED", True, raising=False)
    # Persistence "wired" — any truthy sentinel passes the None guard.
    for name in (
        "tournament_invite_repo",
        "tournament_session_repo",
        "chip_ledger_repo",
        "bankroll_repo",
        "personality_repo",
        "cash_table_repo",
    ):
        monkeypatch.setattr(extensions, name, object(), raising=False)
    monkeypatch.setattr(tournament_invites, "expire_due", lambda **k: [])
    monkeypatch.setattr(tournament_invites, "maybe_offer_main_event", lambda **k: None)

    evt = FakeEvent(created_at="2026-06-02T00:00:00", type="tournament_winner")
    monkeypatch.setattr(
        tournament_ticker, "advance_owner_tournament", lambda **k: {"events": [evt]}
    )
    recorded = []
    monkeypatch.setattr(activity_mod, "record_event", recorded.append)

    ticker_service._maybe_tick_tournament("u1", "sbx1")

    assert recorded == [evt]
