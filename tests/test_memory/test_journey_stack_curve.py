"""stack_curve — per-hand chip stack series for the session sparkline."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from poker.memory.journey import stack_curve


def _player(name, final_stack):
    return SimpleNamespace(name=name, final_stack=final_stack)


def _hand(ts, players):
    return SimpleNamespace(timestamp=datetime.fromisoformat(ts), players=players)


def test_curve_tracks_hero_final_stack_in_order():
    hands = [
        _hand("2026-06-04T00:00:00", [_player("Jeff", 8000), _player("Bob", 8000)]),
        _hand("2026-06-04T00:05:00", [_player("Jeff", 9200), _player("Bob", 6800)]),
        _hand("2026-06-04T00:10:00", [_player("Jeff", 8700), _player("Bob", 7300)]),
    ]
    curve = stack_curve(hands, "Jeff")
    assert [p["value"] for p in curve] == [8000, 9200, 8700]
    assert curve[0]["t"] == "2026-06-04T00:00:00"


def test_hands_without_recorded_stack_are_skipped():
    # A None final_stack is a gap (not a drop to zero) — leave it out.
    hands = [
        _hand("2026-06-04T00:00:00", [_player("Jeff", 8000)]),
        _hand("2026-06-04T00:05:00", [_player("Jeff", None)]),
        _hand("2026-06-04T00:10:00", [_player("Jeff", 9000)]),
    ]
    curve = stack_curve(hands, "Jeff")
    assert [p["value"] for p in curve] == [8000, 9000]


def test_hero_absent_from_a_hand_is_skipped():
    hands = [
        _hand("2026-06-04T00:00:00", [_player("Bob", 8000)]),
        _hand("2026-06-04T00:05:00", [_player("Jeff", 8000), _player("Bob", 8000)]),
    ]
    curve = stack_curve(hands, "Jeff")
    assert [p["value"] for p in curve] == [8000]


def test_empty_when_no_hero_hands():
    assert stack_curve([], "Jeff") == []
