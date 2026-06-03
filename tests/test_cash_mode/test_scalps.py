"""Unit tests for scalp attribution (cash_mode/scalps.py).

Pure — no DB. The attribution rule is the headline-winner heuristic; we feed
tiny fakes (SimpleNamespace) standing in for HandSimResult / HandEvent and
assert the derived (eliminator, victim) pairs, including the documented
skips (self-bust, no-eliminator) and the accepted multiway over-attribution.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cash_mode.scalps import (
    HAND_EVENT_BUST,
    eliminations_from_human_hand,
    eliminations_from_sim,
)

# A non-bust event type for the "ignored" cases — a literal so the pure tests
# don't import the engine. The drift guard below pins the bust constant.
_NON_BUST = "all_in"


def _ev(type_, pid):
    return SimpleNamespace(type=type_, personality_id=pid, opponent_pid=None)


def _result(winner_pid, events):
    return SimpleNamespace(winner_pid=winner_pid, hand_events=events)


# --- eliminations_from_sim (AI-vs-AI world-sim path) -----------------------


def test_single_bust_credits_headline_winner():
    res = _result("ace", [_ev(HAND_EVENT_BUST, "fish")])
    assert eliminations_from_sim(res) == [("ace", "fish")]


def test_self_bust_is_skipped():
    # winner == victim (busted on blinds, nobody covering) → no scalp.
    res = _result("fish", [_ev(HAND_EVENT_BUST, "fish")])
    assert eliminations_from_sim(res) == []


def test_no_headline_winner_yields_nothing():
    res = _result(None, [_ev(HAND_EVENT_BUST, "fish")])
    assert eliminations_from_sim(res) == []


def test_non_bust_events_ignored():
    res = _result("ace", [_ev(_NON_BUST, "fish"), _ev("nice_pot", "whale")])
    assert eliminations_from_sim(res) == []


def test_multiway_credits_winner_for_each_victim():
    # Accepted v1 over-attribution: the headline winner gets every bust.
    res = _result("ace", [_ev(HAND_EVENT_BUST, "fish"), _ev(HAND_EVENT_BUST, "donk")])
    assert eliminations_from_sim(res) == [("ace", "fish"), ("ace", "donk")]


def test_mixed_events_only_busts_count():
    res = _result(
        "ace",
        [
            _ev(_NON_BUST, "ace"),
            _ev(HAND_EVENT_BUST, "fish"),
            _ev(HAND_EVENT_BUST, "ace"),  # self-bust, skipped
        ],
    )
    assert eliminations_from_sim(res) == [("ace", "fish")]


def test_empty_or_missing_events():
    assert eliminations_from_sim(_result("ace", [])) == []
    assert eliminations_from_sim(SimpleNamespace(winner_pid="ace", hand_events=None)) == []


# --- eliminations_from_human_hand (human's real hand) ----------------------


def test_human_busts_each_victim():
    assert eliminations_from_human_hand("guest_jeff", ["fish", "donk"]) == [
        ("guest_jeff", "fish"),
        ("guest_jeff", "donk"),
    ]


def test_human_self_is_filtered():
    assert eliminations_from_human_hand("guest_jeff", ["guest_jeff", "fish"]) == [
        ("guest_jeff", "fish"),
    ]


def test_human_victims_deduped():
    assert eliminations_from_human_hand("guest_jeff", ["fish", "fish"]) == [
        ("guest_jeff", "fish"),
    ]


def test_human_empty_inputs():
    assert eliminations_from_human_hand("guest_jeff", []) == []
    assert eliminations_from_human_hand("", ["fish"]) == []


# --- drift guard: scalps keeps a LOCAL copy of the bust constant to stay pure
# (importing full_sim drags in the whole engine). This test — integration, so
# `--quick` skips it but the full suite/CI catches a rename — pins them equal.
@pytest.mark.integration
def test_bust_constant_matches_full_sim():
    from cash_mode.full_sim import HAND_EVENT_BUST as FS_BUST

    assert HAND_EVENT_BUST == FS_BUST
