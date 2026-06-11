"""preflop_counts / preflop_rates — VPIP, PFR, and starting-hand quality."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from poker.memory.journey import merge_counts, preflop_counts, preflop_rates


def _player(name):
    return SimpleNamespace(name=name, starting_stack=1000, final_stack=None)


def _action(name, action, phase="PRE_FLOP"):
    return SimpleNamespace(player_name=name, action=action, amount=0, phase=phase)


def _hand(hole, actions):
    return SimpleNamespace(
        hand_number=1,
        timestamp=datetime(2026, 6, 4),
        players=[_player("Hero"), _player("Vil")],
        actions=actions,
        hole_cards={"Hero": hole},
        winners=[],
        pot_size=0,
        was_showdown=False,
        community_cards=[],
    )


def test_vpip_counts_voluntary_calls_and_raises_not_blinds():
    hands = [
        _hand(["A♠", "A♦"], [_action("Hero", "raise")]),  # vpip + pfr
        _hand(["K♠", "Q♦"], [_action("Hero", "call")]),  # vpip only
        _hand(["7♠", "2♦"], [_action("Hero", "fold")]),  # neither
        _hand(["9♠", "9♦"], [_action("Hero", "post_blind"), _action("Hero", "check")]),  # neither
    ]
    c = preflop_counts(hands, "Hero")
    assert c["hands"] == 4
    assert c["vpip"] == 2
    assert c["pfr"] == 1
    r = preflop_rates(c)
    assert r["vpip_pct"] == 50
    assert r["pfr_pct"] == 25


def test_premium_and_hand_quality():
    hands = [
        _hand(["A♠", "A♦"], [_action("Hero", "raise")]),  # AA → premium, top 3%
        _hand(["7♠", "2♦"], [_action("Hero", "fold")]),  # 72o → trash, 100
    ]
    c = preflop_counts(hands, "Hero")
    assert c["premium"] == 1
    assert c["pct_n"] == 2
    # avg of 3 (AA) and 100 (72o) ≈ 52
    assert preflop_rates(c)["avg_hand_pct"] == round((3 + 100) / 2)


def test_suited_canonical_quality():
    # AKs is top-10%, KQo is weaker — sanity that suitedness is read.
    hands = [_hand(["A♠", "K♠"], [_action("Hero", "raise")])]
    assert preflop_counts(hands, "Hero")["premium"] == 1  # AKs ∈ PREMIUM


def test_merge_counts_sums_for_overall():
    a = {"hands": 10, "vpip": 4, "pfr": 2, "premium": 1, "pct_sum": 300, "pct_n": 10}
    b = {"hands": 20, "vpip": 10, "pfr": 6, "premium": 2, "pct_sum": 800, "pct_n": 20}
    m = merge_counts([a, b])
    assert m == {"hands": 30, "vpip": 14, "pfr": 8, "premium": 3, "pct_sum": 1100, "pct_n": 30}
    r = preflop_rates(m)
    assert r["vpip_pct"] == round(100 * 14 / 30)
    assert r["avg_hand_pct"] == round(1100 / 30)


def test_no_hands_returns_none():
    assert preflop_rates({"hands": 0}) is None
