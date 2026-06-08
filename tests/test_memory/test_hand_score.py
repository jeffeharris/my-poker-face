"""Hand drama scoring — pure tests over synthetic RecordedHands.

Covers the individual signals (pot magnitude, all-in count, hero risk, cooler
closeness, equity swing / lead changes) and the top_hands ranking.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from poker.equity_snapshot import EquitySnapshot, HandEquityHistory
from poker.memory.hand_score import WEIGHTS, score_hand, top_hands


def _player(name, stack=10_000, human=False):
    return SimpleNamespace(name=name, starting_stack=stack, is_human=human, final_stack=None)


def _action(name, action, amount):
    return SimpleNamespace(player_name=name, action=action, amount=amount, phase="PRE_FLOP")


def _winner(name, amount, hand_name=None, hand_rank=None):
    return SimpleNamespace(name=name, amount_won=amount, hand_name=hand_name, hand_rank=hand_rank)


def _hand(
    *,
    hand_number=1,
    players,
    actions=(),
    winners=(),
    pot_size=0,
    was_showdown=False,
    hole_cards=None,
    community=(),
):
    return SimpleNamespace(
        hand_number=hand_number,
        players=list(players),
        actions=list(actions),
        winners=list(winners),
        pot_size=pot_size,
        was_showdown=was_showdown,
        hole_cards=hole_cards or {},
        community_cards=list(community),
    )


def test_weights_sum_to_100():
    assert sum(WEIGHTS.values()) == pytest.approx(100.0)


def test_bigger_pot_scores_higher():
    small = _hand(players=[_player("A"), _player("B")], pot_size=400, winners=[_winner("A", 400)])
    big = _hand(
        players=[_player("A"), _player("B")], pot_size=40_000, winners=[_winner("A", 40_000)]
    )
    s_small = score_hand(small, "A", big_blind=200).score
    s_big = score_hand(big, "A", big_blind=200).score
    assert s_big > s_small


def test_all_in_count_tag_and_signal():
    hand = _hand(
        players=[_player("A"), _player("B"), _player("C")],
        actions=[_action("A", "all_in", 5000), _action("B", "all_in", 5000)],
        pot_size=10_000,
        winners=[_winner("A", 10_000)],
    )
    sc = score_hand(hand, "A", big_blind=200)
    assert sc.components["all_in"] == 0.7
    assert "2-way all-in" in sc.tags


def test_hero_risk_full_stack():
    hand = _hand(
        players=[_player("A", stack=5000), _player("B")],
        actions=[_action("A", "raise", 5000)],
        pot_size=6000,
        winners=[_winner("A", 6000)],
    )
    sc = score_hand(hand, "A", big_blind=100)
    assert sc.components["hero_risk"] == pytest.approx(1.0)


def test_cooler_closeness_from_showdown_cards():
    # Both make a full house — a genuine cooler; the loser's strong hand should
    # light up the closeness signal and tag it.
    hand = _hand(
        players=[_player("Hero"), _player("Vil")],
        pot_size=20_000,
        was_showdown=True,
        hole_cards={"Hero": ["A♠", "A♦"], "Vil": ["K♠", "K♦"]},
        community=["A♥", "K♥", "K♣", "2d", "2h"],  # hero A's full, vil K's full
        winners=[_winner("Hero", 20_000, "Full House A's over K's", 7)],
    )
    sc = score_hand(hand, "Hero", big_blind=200)
    assert sc.components["closeness"] > 0.5
    assert "cooler" in sc.tags


def _equity(game_id, hn, series):
    """series: list of (street, {name: equity})."""
    snaps = []
    for street, eqs in series:
        for name, eq in eqs.items():
            snaps.append(
                EquitySnapshot(
                    player_name=name, street=street, equity=eq, hole_cards=(), board_cards=()
                )
            )
    return HandEquityHistory(None, game_id, hn, tuple(snaps))


def test_equity_swing_and_suckout():
    hand = _hand(
        players=[_player("Hero"), _player("Vil")],
        pot_size=20_000,
        was_showdown=True,
        winners=[_winner("Hero", 20_000)],
    )
    eq = _equity(
        "g",
        1,
        [
            ("FLOP", {"Hero": 0.20, "Vil": 0.80}),
            ("TURN", {"Hero": 0.18, "Vil": 0.82}),
            ("RIVER", {"Hero": 1.0, "Vil": 0.0}),  # hero rivered it — suckout
        ],
    )
    sc = score_hand(hand, "Hero", big_blind=200, equity=eq)
    assert sc.components["equity_swing"] > 0.5
    assert "suckout" in sc.tags
    assert sc.components["lead_changes"] >= 0.5  # Vil led, then Hero


def test_top_hands_ranks_and_limits():
    hands = [
        _hand(
            hand_number=1,
            players=[_player("A"), _player("B")],
            pot_size=200,
            winners=[_winner("A", 200)],
        ),
        _hand(
            hand_number=2,
            players=[_player("A"), _player("B")],
            actions=[_action("A", "all_in", 9000), _action("B", "all_in", 9000)],
            pot_size=18_000,
            winners=[_winner("A", 18_000)],
        ),
    ]
    ranked = top_hands(hands, "A", big_blind=200, limit=5, min_score=1)
    assert [h.hand_number for h, _ in ranked][0] == 2  # the all-in is more dramatic
    assert len(ranked) <= 5


def test_min_score_filters_quiet_hands():
    quiet = _hand(players=[_player("A"), _player("B")], pot_size=1, winners=[_winner("A", 1)])
    ranked = top_hands([quiet], "A", big_blind=200, min_score=90)
    assert ranked == []
