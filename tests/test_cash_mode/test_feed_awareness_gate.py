"""Tests for the activity-feed awareness gate (Circuit keyring filter).

A career player hears only about rooms on their keyring; roomless/global beats
(tournament, vouch, reputation) always pierce; non-career sandboxes see the full
feed unchanged. See `cash_mode.activity.filter_events_for_player`.
"""

from __future__ import annotations

from cash_mode.activity import (
    EVENT_JOIN,
    EVENT_TOURNAMENT_WINNER,
    EVENT_VOUCH,
    LobbyEvent,
    filter_events_for_player,
)


def _ev(table_id: str, type_: str = EVENT_JOIN) -> LobbyEvent:
    return LobbyEvent(
        type=type_,
        table_id=table_id,
        stake_label="$2",
        personality_id="x",
        name="X",
        reason="",
        message="m",
        created_at="2026-01-01T00:00:00",
    )


def test_career_off_shows_full_feed():
    evs = [_ev("cash-table-2-001"), _ev("cash-table-50-002"), _ev("")]
    out = filter_events_for_player(evs, career_active=False, revealed_table_ids=[])
    assert out == evs


def test_career_on_gates_unrevealed_rooms():
    home = _ev("cash-table-2-001")  # on the keyring → shown
    away = _ev("cash-table-50-002")  # not revealed → hidden
    out = filter_events_for_player(
        [home, away], career_active=True, revealed_table_ids=["cash-table-2-001"]
    )
    assert out == [home]


def test_career_on_pierces_roomless_beats():
    # Tournament beats carry table_id="" (roomless) and must always pierce.
    tourney = _ev("", EVENT_TOURNAMENT_WINNER)
    room = _ev("cash-table-50-002")  # unrevealed → hidden
    out = filter_events_for_player([tourney, room], career_active=True, revealed_table_ids=[])
    assert out == [tourney]


def test_vouch_event_shows_because_room_is_revealed():
    # fire_vouch adds the room to revealed_table_ids before emitting the beat.
    vouch = _ev("cash-table-10-001", EVENT_VOUCH)
    out = filter_events_for_player(
        [vouch], career_active=True, revealed_table_ids=["cash-table-10-001"]
    )
    assert out == [vouch]


def test_empty_revealed_hides_all_room_events_but_keeps_global():
    room = _ev("cash-table-2-001")
    glob = _ev("", EVENT_TOURNAMENT_WINNER)
    out = filter_events_for_player([room, glob], career_active=True, revealed_table_ids=[])
    assert out == [glob]
